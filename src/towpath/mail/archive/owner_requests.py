"""The owner's requests to archive mail (web role), and how far they have got."""

import json
import uuid
from contextlib import closing
from datetime import datetime, timezone

from towpath.mail.archive.common import MAX_ITEMS, ROLE, now
from towpath.stores import open_store, store_path


def request(store_dir, source_id: str, item_ids: list[str], remove: bool, label: str, author: str = "owner") -> str:
    ids = list(dict.fromkeys(item_ids))
    if not ids:
        raise ValueError("choose some messages to archive")
    if len(ids) > MAX_ITEMS:
        raise ValueError(f"archive at most {MAX_ITEMS:,} messages at a time")
    rid = "arc_" + datetime.now(timezone.utc).strftime("%Y%m%d") + "_" + uuid.uuid4().hex[:8]
    with closing(open_store(store_dir, "maildecisions", "web")) as db:
        db.execute("INSERT INTO archive_requests VALUES (?,?,?,?,?,?,?)",
                   (rid, source_id, json.dumps(ids), int(remove), label[:120], author, now()))
        db.commit()
    return rid


def requests(store_dir, source_id: str | None = None, role: str = ROLE) -> list[dict]:
    if not store_path(store_dir, "maildecisions").exists():
        return []
    import sqlite3

    try:
        with closing(open_store(store_dir, "maildecisions", role)) as db:
            rows = [dict(r) for r in db.execute("SELECT * FROM archive_requests" +
                                                (" WHERE source_id = ?" if source_id else "") + " ORDER BY at DESC",
                                                (source_id,) if source_id else ())]
    except sqlite3.OperationalError:
        return []
    for r in rows:
        r["item_ids"] = json.loads(r["item_ids"])
    return rows


def progress(store_dir, request_id: str, role: str = "web") -> dict:
    out = {"archived": 0, "bytes": 0}
    if not store_path(store_dir, "mail").exists():
        return out
    with closing(open_store(store_dir, "mail", role)) as db:
        row = db.execute("SELECT count(*), coalesce(sum(length), 0) FROM archived WHERE request_id = ?",
                         (request_id,)).fetchone()
        state = db.execute("SELECT state FROM archive_progress WHERE request_id = ?", (request_id,)).fetchone()
    return {**(json.loads(state[0]) if state else {}), "archived": row[0], "bytes": row[1]}
