"""Requests the local UI may make, carried out later by towpath-connect.

The UI (web role) only appends to the queue store; towpath-connect reads the queue and does the work:

- **Selected content**: one mail part, through the existing ``content_requests`` table, fetched by
  ``towpath connect fetch-requests`` into the source store's cache. Plain text is shown first; other types are
  described, never rendered.
- **Provider search**: a query to run against the providers (Gmail ``q``, IMAP ``SEARCH``, a files
  content index), through ``search_requests``, run by ``towpath search run-requests``. Responses go to
  the source store's ``search_runs`` for the UI to read, with the time they ran.
"""

import json
from contextlib import closing
from datetime import datetime, timezone

from towpath.canonical import canonical_json, short_id
from towpath.stores import open_store
from towpath.unified.contracts import Filters, split_part

MAX_TEXT_BYTES = 20000
TEXT_TYPES = ("text/plain",)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def search_key(filters: Filters) -> str:
    return short_id("srq", canonical_json(filters.to_dict()))


# -- selected content ---------------------------------------------------------------------------------


def part_state(store_dir, source_id: str, native: str) -> dict:
    """Where one mail part stands: not requested, queued, fetched, or failed. Reads stores only."""
    message, part_id = split_part(native)
    if part_id is None:
        raise ValueError("selected content is requested per part (<ref>#part=<id>)")
    with closing(open_store(store_dir, "source", "web")) as db:
        item = db.execute("SELECT item_id, absent_since_run FROM items WHERE source_id = ? AND native_id = ?",
                          (source_id, message)).fetchone()
        if item is None:
            return {"state": "unknown", "reason": "not in the local index"}
        part = db.execute("SELECT * FROM parts WHERE item_id = ? AND part_id = ?",
                          (item["item_id"], part_id)).fetchone()
        if part is None:
            return {"state": "unknown", "reason": "no such part"}
        fetch = db.execute("SELECT status, detail, at FROM fetches WHERE item_id = ? AND part_id = ? ORDER BY at DESC "
                           "LIMIT 1", (item["item_id"], part_id)).fetchone()
    with closing(open_store(store_dir, "queue", "connect")) as queue:
        queued = queue.execute("SELECT created_at FROM content_requests WHERE item_id = ? AND part_id = ?",
                               (item["item_id"], part_id)).fetchone()
    base = {"item_id": item["item_id"], "part_id": part_id, "mime_type": part["mime_type"], "size": part["size"],
            "sha256": part["sha256"]}
    if item["absent_since_run"]:
        return {**base, "state": "unavailable", "reason": "the message is no longer in the source"}
    if part["sha256"]:
        return {**base, "state": "fetched"}
    if fetch is not None and fetch["status"] != "fetched":
        return {**base, "state": "failed", "reason": fetch["detail"]}
    if queued is not None:
        return {**base, "state": "queued", "since": queued["created_at"]}
    return {**base, "state": "not-requested"}


def request_part(store_dir, source_id: str, native: str, requested_by: str = "ui") -> dict:
    """Queue one part for towpath-connect to fetch. The UI itself never contacts the source."""
    state = part_state(store_dir, source_id, native)
    if state["state"] in {"unknown", "unavailable", "fetched", "queued"}:
        return state
    with closing(open_store(store_dir, "queue", "web")) as queue:
        queue.execute("INSERT OR IGNORE INTO content_requests (item_id, part_id, requested_by, priority, created_at) "
                      "VALUES (?, ?, ?, 'interactive', ?)", (state["item_id"], state["part_id"], requested_by, _now()))
        queue.commit()
    return part_state(store_dir, source_id, native)


def part_text(store_dir, source_id: str, native: str, max_bytes: int = MAX_TEXT_BYTES) -> dict:
    """A fetched plain-text part, decoded with its charset and cut at ``max_bytes``. Untrusted data."""
    state = part_state(store_dir, source_id, native)
    if state["state"] != "fetched":
        return {**state, "text": None}
    if state["mime_type"] not in TEXT_TYPES:
        return {**state, "text": None, "reason": f"{state['mime_type']} is not shown; only plain text is displayed"}
    with closing(open_store(store_dir, "source", "web")) as db:
        row = db.execute("SELECT bytes FROM cache WHERE sha256 = ?", (state["sha256"],)).fetchone()
        charset = db.execute("SELECT charset FROM parts WHERE item_id = ? AND part_id = ?",
                             (state["item_id"], state["part_id"])).fetchone()["charset"]
    if row is None:
        return {**state, "text": None, "reason": "fetched earlier, but the cached copy has been evicted"}
    data = bytes(row["bytes"])
    try:
        text = data.decode(charset or "utf-8", errors="replace")
    except LookupError:
        text = data.decode("utf-8", errors="replace")
    raw = text.encode("utf-8")
    cut = len(raw) > max_bytes
    if cut:
        text = raw[:max_bytes].decode("utf-8", errors="ignore")
    return {**state, "text": text, "cut_at_limit": cut, "content_is_untrusted_data": True}


# -- provider search ----------------------------------------------------------------------------------


def request_search(store_dir, filters: Filters, requested_by: str = "ui") -> str:
    key = search_key(filters)
    with closing(open_store(store_dir, "queue", "web")) as queue:
        queue.execute("INSERT INTO search_requests (request_key, filters, requested_by, created_at) VALUES (?,?,?,?)",
                      (key, json.dumps(filters.to_dict(), sort_keys=True), requested_by, _now()))
        queue.commit()
    return key


def pending_searches(store_dir) -> list[dict]:
    with closing(open_store(store_dir, "source", "web")) as db:
        done = {r["request_seq"] for r in db.execute("SELECT request_seq FROM search_runs")}
    with closing(open_store(store_dir, "queue", "connect")) as queue:
        rows = [dict(r) for r in queue.execute("SELECT * FROM search_requests ORDER BY seq")]
    return [r for r in rows if r["seq"] not in done]


def run_searches(config, limit: int = 20) -> dict:
    """Run every queued provider search once (towpath-connect role) and store each response."""
    from towpath.unified import federation, sources

    pending = pending_searches(config.store_dir)
    latest: dict[str, dict] = {}
    for row in pending:
        latest[row["request_key"]] = row
    ran = 0
    adapters = sources.build_adapters(config, connect=True) if pending else {}
    try:
        with closing(open_store(config.store_dir, "source", "connect")) as db:
            for key, row in latest.items():
                filters = Filters.from_dict(json.loads(row["filters"]))
                response = federation.search(adapters, filters, limit).to_dict()
                at = _now()
                for same in (r for r in pending if r["request_key"] == key):
                    db.execute("INSERT OR REPLACE INTO search_runs VALUES (?,?,?,?)",
                               (same["seq"], key, at, json.dumps(response, sort_keys=True)))
                db.commit()
                ran += 1
    finally:
        federation.close_all(adapters)
    return {"requests": len(pending), "searches_run": ran}


def latest_search(store_dir, filters: Filters) -> dict | None:
    """The most recent stored provider-search response for these filters, if any (read-only)."""
    key = search_key(filters)
    with closing(open_store(store_dir, "source", "web")) as db:
        row = db.execute("SELECT ran_at, response FROM search_runs WHERE request_key = ? ORDER BY ran_at DESC, "
                         "request_seq DESC LIMIT 1", (key,)).fetchone()
    if row is None:
        return None
    return {"ran_at": row["ran_at"], "response": json.loads(row["response"])}


def search_waiting(store_dir, filters: Filters) -> bool:
    key = search_key(filters)
    return any(r["request_key"] == key for r in pending_searches(store_dir))


# -- current policy for stored provider results --------------------------------------------------------


def _mail_reason(db, config, source_ids: dict, result: dict) -> str | None:
    if result["source_id"] not in source_ids:
        return "the source is no longer configured"
    source = source_ids[result["source_id"]]  # None when known only from the store (no configuration)
    mailbox = result["locator"].get("mailbox")
    boxes = getattr(source, "mailboxes", None)
    if mailbox is not None and boxes and mailbox not in boxes:
        return "outside the configured mailboxes"
    message, _ = split_part(result["native"])
    row = db.execute("SELECT absent_since_run FROM items WHERE source_id = ? AND native_id = ?",
                     (result["source_id"], message)).fetchone()
    if row is not None and row["absent_since_run"]:
        return "no longer in the source (as of the last sync)"
    return None  # not yet indexed locally: nothing local contradicts the provider's match


def _file_reason(files_db, config, grants: dict, result: dict) -> str | None:
    from towpath.discovery import refs

    files = getattr(config, "files", None) if config is not None else None
    if config is None:
        return "file results need their grants checked: start the UI with --config"
    if files is None or not files.enabled:
        return "file discovery is disabled"
    provider = files.providers.get(result["source_id"][len("files-"):])
    root, path = result["locator"].get("root"), result["locator"].get("path")
    if provider is None:
        return "the files provider is no longer configured"
    if root not in provider.roots or root not in files.roots:
        return "the root is no longer configured"
    if "search" not in grants.get(root, set()):
        return f"no search grant on root {root} now"
    if refs.excluded(files.roots[root], path or ""):
        return "now excluded by the root's configuration"
    row = files_db.execute("SELECT missing_since_run FROM occurrences WHERE occurrence_id = ?",
                           (result["native"],)).fetchone()
    if row is None or row["missing_since_run"]:
        return "no longer in the files catalog"
    return None


def apply_current_policy(store_dir, config, response: dict) -> dict:
    """A stored provider-search response filtered by today's configuration, grants and catalogs.

    Reads only local configuration and stores (no credential, connector or provider process).
    Withheld results are counted per source with their reasons, never shown, and the answer is
    marked incomplete.
    """
    import copy

    out = copy.deepcopy(response)
    if config is not None:
        source_ids = {sid: s for sid, s in config.sources.items() if s.kind == "mail-provider"}
    else:
        with closing(open_store(store_dir, "source", "web")) as db:
            source_ids = {r["source_id"]: None for r in db.execute(
                "SELECT source_id FROM sources WHERE kind = 'mail-provider'")}
    grants = {}
    if config is not None and getattr(config, "files", None) is not None:
        from towpath.discovery import policy

        grants = policy.granted(config)
    kept, withheld = [], {}
    with closing(open_store(store_dir, "source", "web")) as db, closing(open_store(store_dir, "files", "web")) as fdb:
        for result in out["results"]:
            if result["source_type"] == "files":
                reason = _file_reason(fdb, config, grants, result)
            else:
                reason = _mail_reason(db, config, source_ids, result)
            if reason is None:
                kept.append(result)
            else:
                counts = withheld.setdefault(result["source_id"], {})
                counts[reason] = counts.get(reason, 0) + 1
    out["results"] = kept
    for page in out["sources"]:
        reasons = withheld.get(page["source_id"], {})
        page["result_count"] = sum(1 for r in kept if r["source_id"] == page["source_id"])
        page["withheld"] = sum(reasons.values())
        page["withheld_reasons"] = reasons
    for source_id, reasons in withheld.items():
        detail = "; ".join(f"{n} {reason}" for reason, n in sorted(reasons.items()))
        out["incomplete_because"] = [*out["incomplete_because"],
                                     f"{source_id}: {sum(reasons.values())} stored results withheld ({detail})"]
        out["complete"] = False
    out["policy_checked"] = {"at": _now(), "withheld": sum(sum(r.values()) for r in withheld.values())}
    return out
