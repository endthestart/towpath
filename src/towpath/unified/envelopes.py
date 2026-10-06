"""Wrap existing mail and file records in the result envelope without changing them.

The original record travels unchanged under ``legacy``: a mail item row from the source store, or a
file occurrence (``Occurrence.to_dict()`` or a files-store row). Nothing here reads a store or a source.
"""

import json

from towpath.unified.contracts import Match, Reference, Result, TypedDate, extension_of, part_native

# File extraction status (towpath.discovery.records.STATUSES) -> unified coverage state.
FILE_COVERAGE = {"indexed": "text-extracted", "truncated": "text-extracted", "skipped": "metadata-cataloged",
                 "unsupported": "unsupported", "encrypted": "unreadable", "unreadable": "unreadable",
                 "failed": "failed"}
FILE_DATE_MEANINGS = {"file-modified", "member-modified", "message-date", "document-modified", "indexed-at"}


def _file_parts(occ: dict) -> tuple[str, str, list[dict], str | None]:
    """(root, path, members, provider) from either occurrence shape."""
    if "locator" in occ:
        loc = occ["locator"]
        return loc["root"], loc["path"], [dict(m) for m in loc["members"]], loc.get("provider_id")
    members = occ["members"]
    if isinstance(members, str):
        members = json.loads(members)
    return occ["root_alias"], occ["rel_path"], [dict(m) for m in members], occ.get("provider_id")


def _loads(value, default):
    if value is None:
        return default
    return json.loads(value) if isinstance(value, str) else value


def file_result(source_id: str, occ: dict, depth: str = "catalog", fields: tuple[str, ...] = (),
                provider_rank: int | None = None, availability: str | None = None) -> Result:
    """A file occurrence as a result. Hashes appear only when a provider actually computed them."""
    root, path, members, provider = _file_parts(occ)
    extraction = _loads(occ.get("extraction"), {})
    named = [m for m in members if m.get("name")]
    name = named[-1]["name"] if members and named and members[-1].get("name") else path.rsplit("/", 1)[-1]
    dates = []
    for d in _loads(occ.get("dates"), []):
        if d["meaning"] in FILE_DATE_MEANINGS:
            dates.append(TypedDate(d["meaning"], d["value"], "instant", d.get("basis")))
    if availability is None:
        availability = "missing" if occ.get("missing_since_run") else "present"
    return Result(
        source_id=source_id, source_type="files", ref=Reference(source_id, occ["occurrence_id"]),
        kind="archive-member" if members else "file", title=name,
        match=Match(depth, fields, verified_passage=False, provider_rank=provider_rank),
        version=occ.get("version"), dates=tuple(dates),
        locator={"provider": provider, "root": root, "path": path, "members": members,
                 "display": " > ".join([f"{root}:{path}", *(m.get("name") or f"{m['kind']} {m.get('index')}"
                                                             for m in members)])},
        media_type=occ.get("media_type"), size=occ.get("size"), extension=extension_of(name),
        availability=availability, coverage=FILE_COVERAGE.get(extraction.get("status"), "unknown"),
        restrictions={"excerpt": "needs an excerpt grant on the root"},
        hashes=dict(_loads(occ.get("hashes"), {})), legacy=dict(occ))


def mail_dates(row: dict) -> tuple[TypedDate, ...]:
    dates = []
    if row.get("date_utc"):
        dates.append(TypedDate("message-date", row["date_utc"], "instant", "Date header"))
    internal = row.get("internal_date")
    if internal and str(internal).isdigit():
        from datetime import datetime, timezone

        value = datetime.fromtimestamp(int(internal) / 1000, timezone.utc).isoformat(timespec="seconds")
        dates.append(TypedDate("provider-received", value, "instant", "provider internal date"))
    elif internal:
        dates.append(TypedDate("provider-received", str(internal), "instant", "provider internal date"))
    return tuple(dates)


def mail_result(source_id: str, source_type: str, row: dict, depth: str = "catalog",
                fields: tuple[str, ...] = (), provider_rank: int | None = None, labels: list | None = None,
                version: str | None = None) -> Result:
    """A mail item row from the source store as a result. The body is never part of it."""
    native = row["native_id"]
    locator = {"item_id": row.get("item_id"), "thread_id": row.get("thread_id"),
               "rfc_message_id": row.get("rfc_message_id")}
    if source_type == "imap":
        from towpath.unified.contracts import parse_imap_native

        mailbox, uidvalidity, uid = parse_imap_native(native)
        locator.update(mailbox=mailbox, uidvalidity=uidvalidity, uid=uid)
        version = version or f"UIDVALIDITY={uidvalidity}"
    if labels is not None:
        locator["labels"] = labels
    return Result(
        source_id=source_id, source_type=source_type, ref=Reference(source_id, native), kind="mail-message",
        title=row.get("subject"), match=Match(depth, fields, verified_passage=False, provider_rank=provider_rank),
        version=version, dates=mail_dates(row), locator=locator, size=row.get("size_estimate"),
        availability="absent" if row.get("absent_since_run") else "present", coverage="metadata-cataloged",
        restrictions={"content": "selected-content request through towpath-connect"},
        legacy={k: row[k] for k in row.keys()})


def mail_part_result(source_id: str, source_type: str, row: dict, part: dict, depth: str = "catalog",
                     fields: tuple[str, ...] = ("filename",)) -> Result:
    """One named MIME part (an attachment) as its own result, so type filters reach it."""
    base = mail_result(source_id, source_type, row, depth, fields)
    return Result(
        source_id=source_id, source_type=source_type,
        ref=Reference(source_id, part_native(row["native_id"], part["part_id"])),
        kind="mail-part", title=part.get("filename"), match=base.match, version=base.version, dates=base.dates,
        locator={**base.locator, "part_id": part["part_id"]}, media_type=part.get("mime_type"),
        size=part.get("size"), extension=extension_of(part.get("filename")), availability=base.availability,
        coverage="metadata-cataloged", restrictions=base.restrictions,
        hashes={"sha256": part["sha256"]} if part.get("sha256") else {},
        legacy={"item": base.legacy, "part": dict(part)})
