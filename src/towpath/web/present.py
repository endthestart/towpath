"""Plain-language presentation of search responses for the UI. Pure functions; no store or source access.

The contracts keep precise terms (depths, coverage, completeness reasons); this module only decides how
they read on screen. Nothing is dropped: every incompleteness reason is still shown, in plainer words.
"""

from datetime import datetime, timezone
from email.utils import parseaddr

TYPE_NAMES = {"gmail": "Gmail", "imap": "IMAP mail", "files": "Files"}
# What a search at each depth looked at, per kind of source.
DEPTH_SCOPE = {
    ("catalog", "mail"): "subjects, senders and attachment names",
    ("catalog", "files"): "file names, types and dates",
    ("provider-search", "mail"): "full message text",
    ("provider-search", "files"): "file contents",
    ("content-index", "files"): "file contents",
    ("content-index", "mail"): "message text",
}
FIELD_NAMES = {"from": "sender", "filename": "attachment name", "name": "file name", "path": "folder path",
               "to": "recipient", "cc": "recipient"}


def _family(source_type: str) -> str:
    return "files" if source_type == "files" else "mail"


def source_labels(statuses: list[dict], names: dict[str, str] | None = None) -> dict[str, str]:
    """Friendly names: the name given when the account was added (such as 'Fastmail'), else the type
    ('Gmail'), with the source ID added only when two sources would otherwise share a name."""
    base = {s["source_id"]: (names or {}).get(s["source_id"]) or TYPE_NAMES.get(s["source_type"], s["source_type"])
            for s in statuses}
    counts: dict[str, int] = {}
    for name in base.values():
        counts[name] = counts.get(name, 0) + 1
    return {sid: name if counts[name] == 1 else f"{name} ({sid})" for sid, name in base.items()}


def deep_sources(statuses: list[dict], chosen: list[str]) -> list[str]:
    """Sources (among those searched) that towpath-connect can search inside: mail text or file contents.

    The UI's own adapters report these capabilities as disabled, since only the connector may use them."""
    caps = ("provider-search", "content-search")
    return [s["source_id"] for s in statuses
            if (not chosen or s["source_id"] in chosen) and any(c in s.get("capabilities", {}) for c in caps)]


def join(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1] if names else ""


def _name_only(address: str | None) -> str:
    name, email = parseaddr(address or "")
    return name or email or "(no sender)"


def row(result: dict, url: str) -> dict:
    """One result row: who or what, the title, a short context line and only meaningful tags."""
    kind, legacy = result["kind"], result.get("legacy") or {}
    if kind == "mail-message":
        lead, context = _name_only(legacy.get("from_addr")), None
    elif kind == "mail-part":
        item = legacy.get("item") or {}
        lead = "Attachment"
        context = f"In “{item.get('subject') or '(no subject)'}” from {_name_only(item.get('from_addr'))}"
    else:
        ext = result.get("extension")
        lead = f"{ext.upper()} file" if ext else "File"
        context = result["locator"].get("path") if isinstance(result.get("locator"), dict) else None
    tags = [f"matched {FIELD_NAMES[f]}" for f in result["match"].get("fields", ()) if f in FIELD_NAMES]
    if result.get("availability") == "absent":
        tags.append("no longer in the source")
    elif result.get("availability") not in (None, "present"):
        tags.append(str(result["availability"]))
    date = next((d["value"][:10] for d in result.get("dates", ())), None)
    title = result.get("title") or ("(no subject)" if kind == "mail-message" else "(untitled)")
    return {"url": url, "lead": lead, "title": title, "context": context, "tags": tags, "date": date}


def group(page: dict, rows: list[dict], label: str, source_type: str) -> dict:
    scope = DEPTH_SCOPE.get((page.get("depth"), _family(source_type)))
    count = len(rows)
    summary = f"{count} result{'s' if count != 1 else ''}" if count else "no matches"
    if page.get("more_may_exist"):
        summary += " on this page · more available"
    return {"label": label, "scope": scope, "summary": summary, "rows": rows, "status": page["status"],
            "more": bool(page.get("more_may_exist")),
            "error": page.get("error"), "withheld": page.get("withheld"),
            "withheld_reasons": page.get("withheld_reasons"), "estimate": page.get("estimate")}


def reasons(response: dict, labels: dict[str, str]) -> list[str]:
    """Every incompleteness reason and source note, with source IDs replaced by their friendly names."""
    out = []
    for reason in response.get("incomplete_because", ()):
        sid, sep, rest = reason.partition(": ")
        out.append(f"{labels.get(sid, sid)}: {rest}" if sep and sid in labels else reason)
    for page in response.get("sources", ()):
        for note in page.get("notes", ()):
            out.append(f"{labels.get(page['source_id'], page['source_id'])}: {note}")
    return out


def ago(iso: str | None, now: datetime | None = None) -> str | None:
    if not iso:
        return None
    seconds = int(((now or datetime.now(timezone.utc)) - datetime.fromisoformat(iso)).total_seconds())
    if seconds < 90:
        return "just now"
    for size, unit in ((86400, "day"), (3600, "hour"), (60, "minute")):
        if seconds >= size:
            n = seconds // size
            return f"{n} {unit}{'s' if n != 1 else ''} ago"
    return "just now"


def overview(status: dict, label: str) -> dict:
    """A source on the empty search page: what it holds and when it was last brought up to date."""
    total = sum(v for v in status["coverage"].get("counts", {}).values() if isinstance(v, int))
    noun = "messages" if _family(status["source_type"]) == "mail" else "items"
    fresh = status.get("freshness", {})
    updated = fresh.get("last_success") or fresh.get("last_import")
    line = f"{total:,} {noun} indexed" + (f" · last updated {updated[:10]}" if updated else "")
    return {"label": label, "line": line, "ready": status["state"] == "ready", "reason": status.get("reason")}
