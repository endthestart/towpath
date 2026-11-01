"""Reading one result: checking it is still current, describing it, excerpts, citations and recovery."""

import hashlib
import json
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from towpath.discovery import freshness, policy, refs
from towpath.discovery import store as fstore
from towpath.discovery.providers.base import LimitExceeded, Unavailable
from towpath.discovery.records import Occurrence
from towpath.discovery.service.catalog import stored_record
from towpath.discovery.service.common import (
    DiscoveryError,
    NotFound,
    Stale,
    Unverifiable,
    _accept,
    files_config,
    _limit,
    provider,
)


@dataclass
class State:
    """An occurrence now, judged two separate ways.

    ``provider_version``: does the provider's index still hold the version recorded in files.db
    (``same``, ``changed``, or ``none`` when the provider gives no version)?
    ``source``: does the outer file on disk still match the stamp the index recorded
    (``fresh``, ``changed``, ``missing``, or ``unverifiable``; see towpath.discovery.freshness)?
    Only ``same`` + ``fresh`` is ``current``.
    """

    state: str
    occurrence: Occurrence | None = None
    reason: str | None = None
    provider_version: str | None = None
    source: str | None = None
    checked: list = field(default_factory=list)
    indexed_stamp: dict | None = None
    live_stamp: dict | None = None

    def to_dict(self) -> dict:
        return {"state": self.state, "reason": self.reason, "provider_version": self.provider_version,
                "source": self.source, "source_fields_checked": self.checked,
                "indexed_source_stamp": self.indexed_stamp, "live_source_stamp": self.live_stamp}


def _source_path(config, row: dict):
    return refs.resolve_in_root(config.files.roots[row["root_alias"]], row["rel_path"])


def current_state(config, row: dict) -> State:
    """Ask the provider about the occurrence now, and check the source file against the index."""
    fc = config.files
    prov = provider(config, row["provider_id"])
    try:
        hit = prov.describe(row["native_id"], fc.limits["timeout_seconds"])
    except Unavailable as exc:
        return State("unavailable", reason=str(exc))
    occ = _accept(hit, prov, {row["root_alias"]})
    if occ is None or occ.occurrence_id != row["occurrence_id"]:
        return State("unavailable", reason="the provider's reference no longer points at this occurrence")
    try:
        live = freshness.live_stamp(_source_path(config, row))
    except refs.BadReference:
        return State("unavailable", reason="the source no longer resolves inside its root")
    source, checked, source_reason = freshness.compare(hit.source_stamp, live)
    if occ.version is None or row["version"] is None:
        version = "none"
    else:
        version = "same" if occ.version == row["version"] else "changed"
    st = State("current", occ, None, version, source, checked, hit.source_stamp, live)
    if source == "missing":
        st.state, st.reason = "unavailable", source_reason
    elif version == "changed":
        st.state, st.reason = "changed", "the provider's index holds a different version than was recorded"
    elif source == "changed":
        st.state, st.reason = "changed", source_reason
    elif version == "none":
        st.state, st.reason = "unverifiable", "the provider gives no version token"
    elif source == "unverifiable":
        st.state, st.reason = "unverifiable", source_reason
    return st


def require_current(config, row: dict) -> State:
    """The occurrence's state, raising unless it is current. Content is read only after this."""
    st = current_state(config, row)
    if st.state == "changed":
        raise Stale(f"{row['occurrence_id']}: {st.reason}")
    if st.state == "unavailable":
        raise Unavailable(st.reason)
    if st.state == "unverifiable":
        raise Unverifiable(f"{row['occurrence_id']}: {st.reason}; no content is returned without a check")
    return st


def _unchanged_since(config, row: dict, st: State) -> None:
    """After reading content: the source must not have changed during the read."""
    if not freshness.same(freshness.live_stamp(_source_path(config, row)), st.live_stamp):
        raise Stale(f"{row['occurrence_id']}: the source file changed while it was being read")


def describe(config, occurrence_id: str) -> dict:
    row = stored_record(config, occurrence_id, "search")
    st = current_state(config, row)
    return {"occurrence": row, **st.to_dict(), "current": st.occurrence.to_dict() if st.occurrence else None}


def _cut_utf8(text: str, max_bytes: int) -> tuple[str, bool]:
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text, False
    return raw[:max_bytes].decode("utf-8", errors="ignore"), True


def excerpt(config, occurrence_id: str, start: int = 0, max_bytes: int | None = None) -> dict:
    fc = files_config(config)
    row = stored_record(config, occurrence_id, "excerpt")
    max_bytes = _limit(fc, "max_excerpt_bytes", max_bytes)
    if start < 0:
        raise DiscoveryError("start must be 0 or more")
    st = require_current(config, row)
    ex = provider(config, row["provider_id"]).excerpt(row["native_id"], start, max_bytes,
                                                       fc.limits["timeout_seconds"])
    _unchanged_since(config, row, st)
    text, cut = _cut_utf8(ex.text, max_bytes)
    location = {"kind": "text-offset", "start": ex.start, "length": len(text)}
    db = fstore.connect_rw(config)
    try:
        citation = fstore.add_citation(db, occurrence_id, row["version"], st.live_stamp, location,
                                       hashlib.sha256(text.encode("utf-8")).hexdigest())
    finally:
        db.close()
    return {
        "occurrence_id": occurrence_id, "state": st.state, "source": st.source, "citation": citation, "text": text,
        "content_is_untrusted_data": True, "excerpt_cut_at_limit": cut, "max_bytes": max_bytes,
        "source_extraction_truncated": ex.truncated_source, "source_extracted_bytes": ex.extracted_bytes,
    }


def resolve_citation(config, citation) -> dict:
    """Is a citation still good? Never serves different content under an old citation."""
    if isinstance(citation, str):
        db = fstore.connect_ro(config)
        try:
            found = fstore.get_citation(db, citation)
        finally:
            db.close()
        if found is None:
            raise NotFound(f"no citation {citation}")
        citation = found
    try:
        row = stored_record(config, citation["occurrence_id"], "search")
    except NotFound:
        return {"citation": citation, "state": "unavailable", "reason": "occurrence not in the catalog"}
    st = current_state(config, row)
    result = {"citation": citation, "state": st.state, "reason": st.reason, "text_verified": False,
              **{k: v for k, v in st.to_dict().items() if k not in {"state", "reason"}}}
    if st.state == "changed":
        result["state"] = "stale"
    elif st.state == "current":
        if row["version"] != citation.get("version"):
            result.update(state="stale", reason="the provider's version differs from the one cited")
        elif citation.get("source") is None:
            result.update(state="unverifiable", reason="the citation records no source stamp")
        elif not freshness.same(citation["source"], st.live_stamp):
            result.update(state="stale", reason="the source file differs from the one cited")
    if result["state"] == "current" and policy.allowed(config, row["root_alias"], "excerpt"):
        loc = citation["location"]
        ex = provider(config, row["provider_id"]).excerpt(row["native_id"], loc["start"], max(loc["length"], 1) * 4,
                                                           config.files.limits["timeout_seconds"])
        text = ex.text[:loc["length"]]
        if not freshness.same(freshness.live_stamp(_source_path(config, row)), st.live_stamp):
            result.update(state="stale", reason="the source file changed while it was being read")
        elif hashlib.sha256(text.encode("utf-8")).hexdigest() != citation["excerpt_sha256"]:
            result.update(state="stale", reason="the cited text no longer matches")
        else:
            result["text_verified"] = True
    return result


def recover(config, occurrence_id: str) -> dict:
    """Write a derived copy of one occurrence to ``recover_dir``, with a provenance record."""
    fc = files_config(config)
    row = stored_record(config, occurrence_id, "recover")
    st = require_current(config, row)
    base = Path(os.path.realpath(fc.recover_dir))
    for root in fc.roots.values():
        real_root = Path(os.path.realpath(root.path))
        if base == real_root or real_root in base.parents or base in real_root.parents:
            raise refs.BadReference("recover_dir resolves inside or around a files root")
    version_key = hashlib.sha256(json.dumps([row["version"], st.live_stamp], sort_keys=True).encode()).hexdigest()[:12]
    folder = base / occurrence_id / version_key
    folder.mkdir(parents=True, exist_ok=True)
    name = refs.safe_filename(row["members"][-1]["name"] if row["members"] else row["rel_path"])
    partial = folder / f".partial-{uuid.uuid4().hex}"
    try:
        provider(config, row["provider_id"]).recover(row["native_id"], partial, fc.limits["max_recover_bytes"],
                                                      fc.limits["timeout_seconds"])
        _unchanged_since(config, row, st)
        data_size = partial.stat().st_size
        if data_size > fc.limits["max_recover_bytes"]:
            raise LimitExceeded("recovered copy is larger than max_recover_bytes")
        digest = hashlib.sha256(partial.read_bytes()).hexdigest()
        dest = folder / name
        if dest.exists() and hashlib.sha256(dest.read_bytes()).hexdigest() != digest:
            dest = folder / f"{digest[:12]}-{name}"
        if dest.exists():
            partial.unlink()
        else:
            os.replace(partial, dest)
            os.chmod(dest, 0o444)
    finally:
        if partial.exists():
            partial.unlink()
    claimed = row["hashes"].get("sha256")
    provenance = {
        "format": "towpath.files.recovery/1", "occurrence_id": occurrence_id, "provider": row["provider_id"],
        "native_id": row["native_id"], "root": row["root_alias"], "path": row["rel_path"], "members": row["members"],
        "version": row["version"], "version_state": st.state, "source": st.source,
        "source_stamp": st.live_stamp, "source_fields_checked": st.checked, "sha256": digest, "size": data_size,
        "provider_sha256": claimed, "matches_provider_hash": None if claimed is None else claimed == digest,
        "recovered_at": fstore.now(), "file": dest.name,
    }
    (folder / f"{dest.name}.provenance.json").write_text(json.dumps(provenance, indent=2, sort_keys=True))
    db = fstore.connect_rw(config)
    try:
        provenance["recovery_id"] = fstore.add_recovery(db, occurrence_id, row["version"], str(dest), digest,
                                                        data_size)
    finally:
        db.close()
    provenance["recovered_to"] = str(dest)
    return provenance
