"""What the connection pages share: the stores, a connection's status, and how a connection is shown."""

from django.conf import settings
from django.http import Http404

from towpath import connections

STATUS = {
    "choose-folders": ("Choose folders to finish setup", "warn"),
    "requested": ("Starting to index…", "busy"),
    "running": ("Indexing…", "busy"),
    "paused": ("Paused", "warn"),
    "disconnected": ("Disconnected", "warn"),
}


def _store():
    return settings.TOWPATH_STORE_DIR


def _creds():
    return settings.TOWPATH_CREDENTIALS_DIR


def _config_ids() -> set[str]:
    config = getattr(settings, "TOWPATH_CONFIG", None)
    return set(config.sources) if config is not None else set()


def status(c: dict) -> dict:
    """Plain words and a tone for a connection's current state."""
    if c["state"] in ("choose-folders", "disconnected"):
        text, tone = STATUS[c["state"]]
    elif c["indexing"] in STATUS:
        text, tone = STATUS[c["indexing"]]
    elif c["last_error"]:
        text, tone = "Needs attention", "warn"
    elif c["progress"]:
        text, tone = "Indexed", "ok"
    else:
        text, tone = "Ready to index", "ok"
    return {"text": text, "tone": tone, "busy": c["indexing"] in ("requested", "running")}


def _typed_password(request, provider: str) -> str:
    """Exactly what was typed, except that Fastmail's grouped app passwords lose their spaces."""
    typed = request.POST.get("password", "")
    return typed.replace(" ", "").strip() if provider in connections.APP_PASSWORDS else typed


def _redirect_uri(request) -> str:
    base = getattr(settings, "TOWPATH_PUBLIC_URL", None) or f"{request.scheme}://{request.get_host()}"
    return base.rstrip("/") + "/connections/google/callback"


def _folders_view(c: dict) -> dict:
    progress = c["progress"] or {}
    roots = c["settings"].get("roots") or []
    busy = c["indexing"] in ("requested", "running")
    phase = progress.get("phase")
    again = phase == "reading" and bool(progress.get("in_search"))  # files already in search are read only if changed
    text = {"reading": "Checking for changes…" if again else "Reading files…", "adding": "Adding to search…",
            "measuring": "Measuring space…"}.get(phase, "Starting…") if busy else None
    count = lambda key: f"{progress[key]:,}" if isinstance(progress.get(key), int) and progress[key] else None  # noqa: E731
    view = {**c, "status": {**status(c), **({"text": text} if text else {})},
            "address": f"{len(roots)} folder{'s' if len(roots) != 1 else ''}",
            "folders_chosen": [r["rel"] for r in roots], "indexed": progress.get("in_search"),
            "rate": f"{progress['rate']['per_minute']:,}" if (progress.get("rate") or {}).get("per_minute") is not None
            else None,
            "indexed_text": f"{progress['in_search']:,}" if progress.get("in_search") is not None else None,
            # Recoll's total grows while it walks; it is an estimate only when it runs ahead of the files read.
            "files_text": count("files"),
            "total_text": count("total") if (progress.get("total") or 0) > (progress.get("files") or 0) else None,
            "errors": progress.get("errors") or 0,
            "again": again, "changed_text": count("docs") or "none",
            "phase": phase}
    when = progress.get("at")
    view["updated"] = when[:16].replace("T", " ") + " UTC" if when else None
    return view


def _view(c: dict) -> dict:
    if c["adapter"] == "recoll":
        return _folders_view(c)
    if c["adapter"] == "model":
        tested = c["settings"].get("tested") or {}
        gone = c["state"] == "disconnected"
        return {**c, "status": {"text": "Disconnected" if gone else "Connected", "tone": "warn" if gone else "ok",
                                "busy": False},
                "address": c["settings"]["base_url"], "indexed": None, "model": c["settings"]["model"],
                "cloud": c["settings"].get("destination") == "third-party",
                "further": [{"name": k, "cloud": v.get("destination") == "third-party",
                             "seconds": (v.get("tested") or {}).get("seconds")}
                            for k, v in sorted((c["settings"].get("models") or {}).items())],
                "tested_text": (f"answered in {tested['seconds']} s, {tested['at'][:16].replace('T', ' ')} UTC"
                                if tested else None)}
    progress = c["progress"] or {}
    rate = (progress.get("rate") or {}).get("per_minute")
    when = progress.get("at")
    return {**c, "status": status(c), "address": c["settings"].get("username") or f"{c['display_name']} account",
            "indexed": progress.get("indexed"),
            "indexed_text": f"{progress['indexed']:,}" if progress.get("indexed") is not None else None,
            "rate": round(rate) if rate else None, "updated": when[:16].replace("T", " ") + " UTC" if when else None,
            "folders_chosen": c["settings"].get("mailboxes") or [], "write": c["settings"].get("write")}


def _get(sid: str) -> dict:
    found = connections.get(_store(), sid)
    if found is None:
        raise Http404
    return found


def _flat(nodes: list[dict], depth: int = 0) -> list[dict]:
    out = []
    for n in nodes:
        out.append({"rel": n["rel"], "name": n["name"], "depth": depth, "readable": n["readable"]})
        out += _flat(n["children"], depth + 1)
    return out
