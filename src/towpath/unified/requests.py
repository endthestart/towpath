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
    if part["sha256"]:
        return {**base, "state": "fetched"}
    if fetch is not None and fetch["status"] != "fetched":
        return {**base, "state": "failed", "reason": fetch["detail"]}
    if item["absent_since_run"]:
        return {**base, "state": "unavailable", "reason": "the message is no longer in the source"}
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
