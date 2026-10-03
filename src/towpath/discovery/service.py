"""File discovery operations: probe, status, search, describe, excerpt, recover, import, citations.

Runs as the connect role (writes ``files.db``; reads grants from the
decisions store). Every provider row is re-checked here: it must resolve inside
a configured root, not be excluded, and come from a root with the needed grant.
Rows that fail are dropped before anything is counted, so a broad provider index
cannot leak results, totals, or snippets from places without a grant.
"""

import hashlib
import json
import os
import time
import uuid
from collections import Counter
from pathlib import Path

from towpath.discovery import policy, refs
from towpath.discovery import store as fstore
from towpath.discovery.providers import build
from towpath.discovery.providers.base import LimitExceeded, Unavailable
from towpath.discovery.records import Locator, Occurrence, RecordError


class DiscoveryError(RuntimeError):
    code = "error"


class Disabled(DiscoveryError):
    code = "disabled"


class NotFound(DiscoveryError):
    code = "not-found"


class Stale(DiscoveryError):
    code = "stale"


ERROR_CODES = {policy.Denied: "denied", Unavailable: "unavailable", refs.BadReference: "invalid-reference",
               LimitExceeded: "limit"}


def error_code(exc: BaseException) -> str:
    if isinstance(exc, DiscoveryError):
        return exc.code
    for kind, code in ERROR_CODES.items():
        if isinstance(exc, kind):
            return code
    return "error"


def files_config(config):
    if config.files is None:
        raise Disabled("file discovery is not configured (no [files] table)")
    if not config.files.enabled:
        raise Disabled("file discovery is disabled ([files] enabled = false)")
    return config.files


def provider(config, provider_id: str | None = None):
    fc = files_config(config)
    if not fc.providers:
        raise Unavailable("no files provider is configured")
    if provider_id is None:
        if len(fc.providers) > 1:
            raise DiscoveryError(f"several providers configured; choose one of {', '.join(fc.providers)}")
        provider_id = next(iter(fc.providers))
    if provider_id not in fc.providers:
        raise NotFound(f"no files provider {provider_id!r}")
    return build(fc.providers[provider_id], fc)


def _limit(fc, name: str, requested: int | None) -> int:
    ceiling = fc.limits[name]
    if requested is None:
        return ceiling
    if requested < 1:
        raise DiscoveryError(f"{name} must be at least 1")
    return min(requested, ceiling)


def _accept(hit, prov, roots: set[str]) -> Occurrence | None:
    """Turn an untrusted provider row into an occurrence, or None if it must not be shown."""
    try:
        alias, rel = refs.locate(hit.url, prov.roots)
    except refs.BadReference:
        return None
    if alias not in roots or refs.excluded(prov.roots[alias], rel):
        return None
    try:
        locator = Locator(prov.id, alias, rel, hit.members, hit.native_id)
        return Occurrence(locator, hit.extraction, hit.media_type, hit.size, hit.dates, hit.hashes, hit.version)
    except RecordError:
        return None


def probe(config, provider_id: str | None = None) -> list[dict]:
    fc = files_config(config)
    ids = [provider_id] if provider_id else list(fc.providers)
    out = []
    for pid in ids:
        prov = provider(config, pid)
        entry = {"provider": pid, "adapter": prov.adapter, "roots": list(prov.roots),
                 "capabilities": dict(prov.capabilities)}
        try:
            entry.update(available=True, **prov.probe(fc.limits["timeout_seconds"]))
        except Unavailable as exc:
            entry.update(available=False, reason=str(exc))
        out.append(entry)
    return out


def status(config) -> dict:
    """Configuration, grants, and catalog state. Calls no provider and reads no source file."""
    if config.files is None:
        return {"configured": False}
    fc = config.files
    grants = policy.granted(config)
    db = fstore.connect_ro(config)
    try:
        counts = [dict(r) for r in db.execute(
            "SELECT provider_id, root_alias, count(*) AS occurrences, sum(missing_since_run IS NOT NULL) AS missing "
            "FROM occurrences GROUP BY provider_id, root_alias")]
        runs = [dict(r) for r in db.execute(
            "SELECT run_id, provider_id, kind, started_at, finished_at, termination, reason, items_seen FROM runs "
            "WHERE kind = 'import' ORDER BY started_at DESC LIMIT 5")]
        coverage = [dict(r) | {"by_status": json.loads(r["by_status"])} for r in db.execute(
            "SELECT * FROM coverage ORDER BY rowid DESC LIMIT 5")]
    finally:
        db.close()
    return {
        "configured": True, "enabled": fc.enabled, "limits": fc.limits,
        "roots": {a: {"exclude": list(r.exclude), "grants": sorted(grants.get(a, ()))} for a, r in fc.roots.items()},
        "providers": {p.id: {"adapter": p.adapter, "roots": list(p.roots)} for p in fc.providers.values()},
        "catalog": counts, "recent_imports": runs, "recent_coverage": coverage,
    }


def _result(occ: Occurrence, passage: dict | None) -> dict:
    data = occ.to_dict()
    data["passage"] = passage
    return data


def search(config, query: str, provider_id: str | None = None, limit: int | None = None,
           offset: int = 0) -> dict:
    fc = files_config(config)
    if not query.strip():
        raise DiscoveryError("empty query")
    if offset < 0 or offset > fc.limits["max_scan"]:
        raise DiscoveryError(f"offset must be between 0 and {fc.limits['max_scan']}")
    prov = provider(config, provider_id)
    limit = _limit(fc, "max_results", limit)
    grants = policy.granted(config)
    roots = {a for a in prov.roots if "search" in grants.get(a, set())}
    if not roots:
        raise policy.Denied(f"no search grant for any root of provider {prov.id}")
    timeout = fc.limits["timeout_seconds"]
    deadline = time.monotonic() + timeout
    db = fstore.connect_rw(config)
    run = fstore.begin_run(db, prov.id, "search")
    results, rows, permitted, more, stopped = [], 0, 0, False, None
    try:
        for hit in prov.search(query, [prov.roots[a] for a in sorted(roots)], fc.limits["max_scan"], timeout):
            rows += 1
            occ = _accept(hit, prov, roots)
            if occ is None:
                continue
            permitted += 1
            if permitted <= offset:
                continue
            if len(results) >= limit:
                more = True
                break
            fstore.observe(db, occ, run)
            results.append(_result(occ, hit.passage))
            if time.monotonic() > deadline:
                stopped, more = "time limit reached", True
                break
        # A provider that returned max_scan rows may hold more permitted results beyond them.
        more = more or rows >= fc.limits["max_scan"]
        db.commit()
        fstore.finish_run(db, run, "complete" if stopped is None else "partial", len(results), stopped)
    except KeyboardInterrupt:
        db.commit()
        fstore.finish_run(db, run, "interrupted", len(results), "cancelled")
        raise
    except (Unavailable, LimitExceeded) as exc:
        fstore.finish_run(db, run, "failed", len(results), error_code(exc))
        raise
    finally:
        db.close()
    return {"query": query, "provider": prov.id, "roots": sorted(roots), "offset": offset, "limit": limit,
            "results": results, "more_may_exist": more, "stopped": stopped, "run_id": run,
            "note": "results carry no file text; use 'files excerpt' (needs an excerpt grant)"}


def _stored(config, occurrence_id: str, feature: str):
    """The stored occurrence, if its root still exists, is not excluded, and has ``feature`` granted."""
    fc = files_config(config)
    db = fstore.connect_ro(config)
    try:
        row = fstore.get(db, occurrence_id)
    finally:
        db.close()
    if row is None or row["root_alias"] not in fc.roots or row["provider_id"] not in fc.providers:
        raise NotFound(f"no occurrence {occurrence_id}")
    if refs.excluded(fc.roots[row["root_alias"]], row["rel_path"]):
        raise NotFound(f"no occurrence {occurrence_id}")
    policy.require(config, row["root_alias"], feature)
    return fstore.row_to_dict(row)


def _current(config, row: dict):
    """Ask the provider for the occurrence now. Returns (state, occurrence or None, reason)."""
    fc = config.files
    prov = provider(config, row["provider_id"])
    try:
        hit = prov.describe(row["native_id"], fc.limits["timeout_seconds"])
    except Unavailable as exc:
        return "unavailable", None, str(exc)
    occ = _accept(hit, prov, {row["root_alias"]})
    if occ is None or occ.occurrence_id != row["occurrence_id"]:
        return "unavailable", None, "the provider's reference no longer points at this occurrence"
    if not os.path.exists(refs.resolve_in_root(fc.roots[row["root_alias"]], row["rel_path"])):
        return "unavailable", None, "the file is gone"
    if occ.version is None or row["version"] is None:
        return "unverifiable", occ, "the provider gives no version token"
    if occ.version != row["version"]:
        return "changed", occ, "changed since it was recorded"
    return "current", occ, None


def describe(config, occurrence_id: str) -> dict:
    row = _stored(config, occurrence_id, "search")
    state, occ, reason = _current(config, row)
    return {"occurrence": row, "state": state, "reason": reason,
            "current": occ.to_dict() if occ is not None else None}


def _cut_utf8(text: str, max_bytes: int) -> tuple[str, bool]:
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text, False
    return raw[:max_bytes].decode("utf-8", errors="ignore"), True


def excerpt(config, occurrence_id: str, start: int = 0, max_bytes: int | None = None) -> dict:
    fc = files_config(config)
    row = _stored(config, occurrence_id, "excerpt")
    state, _, reason = _current(config, row)
    if state == "changed":
        raise Stale(f"{occurrence_id} {reason}; search again")
    if state == "unavailable":
        raise Unavailable(reason)
    max_bytes = _limit(fc, "max_excerpt_bytes", max_bytes)
    if start < 0:
        raise DiscoveryError("start must be 0 or more")
    ex = provider(config, row["provider_id"]).excerpt(row["native_id"], start, max_bytes,
                                                       fc.limits["timeout_seconds"])
    text, cut = _cut_utf8(ex.text, max_bytes)
    location = {"kind": "text-offset", "start": ex.start, "length": len(text)}
    db = fstore.connect_rw(config)
    try:
        citation = fstore.add_citation(db, occurrence_id, row["version"], location,
                                       hashlib.sha256(text.encode("utf-8")).hexdigest())
    finally:
        db.close()
    return {
        "occurrence_id": occurrence_id, "state": state, "citation": citation, "text": text,
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
        row = _stored(config, citation["occurrence_id"], "search")
    except NotFound:
        return {"citation": citation, "state": "unavailable", "reason": "occurrence not in the catalog"}
    state, _, reason = _current(config, row)
    if state == "current" and row["version"] != citation["version"]:
        state, reason = "stale", "the occurrence has changed since this citation was made"
    elif state == "changed":
        state = "stale"
    result = {"citation": citation, "state": state, "reason": reason, "text_verified": False}
    if state == "current" and policy.allowed(config, row["root_alias"], "excerpt"):
        loc = citation["location"]
        ex = provider(config, row["provider_id"]).excerpt(row["native_id"], loc["start"], max(loc["length"], 1) * 4,
                                                           config.files.limits["timeout_seconds"])
        text = ex.text[:loc["length"]]
        if hashlib.sha256(text.encode("utf-8")).hexdigest() != citation["excerpt_sha256"]:
            result.update(state="stale", reason="the cited text no longer matches")
        else:
            result["text_verified"] = True
    return result


def recover(config, occurrence_id: str) -> dict:
    """Write a derived copy of one occurrence to ``recover_dir``, with a provenance record."""
    fc = files_config(config)
    row = _stored(config, occurrence_id, "recover")
    state, _, reason = _current(config, row)
    if state == "changed":
        raise Stale(f"{occurrence_id} {reason}; search again")
    if state == "unavailable":
        raise Unavailable(reason)
    base = Path(os.path.realpath(fc.recover_dir))
    for root in fc.roots.values():
        real_root = Path(os.path.realpath(root.path))
        if base == real_root or real_root in base.parents or base in real_root.parents:
            raise refs.BadReference("recover_dir resolves inside or around a files root")
    version_key = hashlib.sha256((row["version"] or "unversioned").encode()).hexdigest()[:12]
    folder = base / occurrence_id / version_key
    folder.mkdir(parents=True, exist_ok=True)
    name = refs.safe_filename(row["members"][-1]["name"] if row["members"] else row["rel_path"])
    partial = folder / f".partial-{uuid.uuid4().hex}"
    try:
        provider(config, row["provider_id"]).recover(row["native_id"], partial, fc.limits["max_recover_bytes"],
                                                      fc.limits["timeout_seconds"])
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
        "version": row["version"], "version_state": state, "sha256": digest, "size": data_size,
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


def import_catalog(config, provider_id: str | None = None, root: str | None = None,
                   max_items: int = 10000) -> list[dict]:
    """Copy references (never text) for granted roots into files.db, recording coverage honestly."""
    fc = files_config(config)
    prov = provider(config, provider_id)
    if root is not None:
        if root not in prov.roots:
            raise NotFound(f"provider {prov.id} has no root {root!r}")
        policy.require(config, root, "search")
        targets = [root]
    else:
        grants = policy.granted(config)
        targets = [a for a in prov.roots if "search" in grants.get(a, set())]
        if not targets:
            raise policy.Denied(f"no search grant for any root of provider {prov.id}")
    reports = []
    for alias in targets:
        db = fstore.connect_rw(config)
        run = fstore.begin_run(db, prov.id, "import")
        statuses, refused, seen, changed = Counter(), 0, 0, 0
        complete, reason, termination = True, None, "complete"
        deadline = time.monotonic() + fc.limits["timeout_seconds"]
        try:
            for n, hit in enumerate(prov.enumerate(prov.roots[alias], max_items + 1, fc.limits["timeout_seconds"])):
                if n >= max_items:
                    complete, reason = False, f"stopped at max_items={max_items}"
                    break
                if time.monotonic() > deadline:
                    complete, reason = False, "time limit reached"
                    break
                try:
                    located, rel = refs.locate(hit.url, prov.roots)
                except refs.BadReference:
                    refused += 1
                    continue
                if located == alias and refs.excluded(prov.roots[alias], rel):
                    continue
                occ = _accept(hit, prov, {alias})
                if occ is None:
                    refused += 1
                    continue
                seen += 1
                statuses[occ.extraction.status] += 1
                changed += fstore.observe(db, occ, run) == "changed"
        except KeyboardInterrupt:
            complete, reason, termination = False, "cancelled", "interrupted"
            db.commit()
            fstore.record_coverage(db, run, prov.id, alias, False, seen, dict(statuses), reason)
            fstore.finish_run(db, run, termination, seen, reason)
            db.close()
            raise
        except (Unavailable, LimitExceeded) as exc:
            complete, reason, termination = False, f"{error_code(exc)}: {exc}", "failed"
        if termination == "complete" and not complete:
            termination = "partial"
        missing = fstore.mark_missing(db, prov.id, alias, run) if complete else 0
        db.commit()
        fstore.record_coverage(db, run, prov.id, alias, complete, seen, dict(statuses), reason)
        fstore.finish_run(db, run, termination, seen, reason)
        db.close()
        reports.append({"provider": prov.id, "root": alias, "run_id": run, "termination": termination,
                        "complete": complete, "reason": reason, "occurrences_seen": seen, "changed": changed,
                        "by_status": dict(statuses), "refused_references": refused, "marked_missing": missing,
                        "absence_established": complete})
    return reports
