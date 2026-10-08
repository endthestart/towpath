"""Queries of existing metadata, always opened under the read-only web role."""

from contextlib import closing
from datetime import datetime, timezone
import json
import math
from pathlib import Path

from towpath.stores import open_store, store_path

PAGE_SIZE = 25
VIEWS = {"all": "All mail", "inbox": "Inbox", "sent": "Sent", "attachments": "Attachments"}


def connection(store_dir: Path):
    return open_store(store_dir, "source", "web")


_overviews: dict = {}


def overview(store_dir: Path) -> dict:
    """Mail totals for the Overview page. Counting labels reads every message's record (about two seconds for a
    quarter of a million), so the answer is kept until the store file changes, which only a sync does."""
    path = store_path(store_dir, "source")
    try:
        st = path.stat()
        key = (str(path), st.st_mtime_ns, st.st_size)
    except OSError:
        key = None
    if key is not None and key in _overviews:
        return _overviews[key]
    result = _overview(store_dir)
    if key is not None:
        _overviews.clear()
        _overviews[key] = result
    return result


def _overview(store_dir: Path) -> dict:
    with closing(connection(store_dir)) as db:
        total = db.execute("SELECT count(*) FROM items WHERE absent_since_run IS NULL").fetchone()[0]
        labels = dict(db.execute("""
            SELECT j.value, count(DISTINCT i.item_id) FROM items i
            JOIN observations o ON o.item_id=i.item_id AND o.run_id=i.last_seen_run
            JOIN json_each(o.labels) j WHERE i.absent_since_run IS NULL
            AND j.value IN ('INBOX','SENT','UNREAD') GROUP BY j.value
        """))
        attachments = db.execute("""SELECT count(*) FROM parts p JOIN items i USING(item_id)
            WHERE i.absent_since_run IS NULL AND p.filename IS NOT NULL AND p.filename != ''""").fetchone()[0]
        latest = db.execute("""SELECT r.kind,r.started_at,r.finished_at,r.complete,r.termination
            FROM runs r WHERE r.source_id IN (SELECT DISTINCT source_id FROM items)
            ORDER BY r.seq DESC LIMIT 1""").fetchone()
        latest = dict(latest) if latest else None
        if latest:
            for field in ("started_at", "finished_at"):
                latest[field] = datetime.fromisoformat(latest[field]) if latest[field] else None
        return {"total": total, "inbox": labels.get("INBOX", 0), "sent": labels.get("SENT", 0),
                "unread": labels.get("UNREAD", 0), "attachments": attachments,
                "cached_parts": db.execute("SELECT count(*) FROM cache").fetchone()[0],
                "latest": latest}


def _display(row) -> dict:
    item = dict(row)
    item["labels"] = json.loads(item.get("labels") or "[]")
    item["display_date"] = None
    try:
        if item.get("internal_date"):
            item["display_date"] = datetime.fromtimestamp(int(item["internal_date"]) / 1000, timezone.utc)
        elif item.get("date_utc"):
            item["display_date"] = datetime.fromisoformat(item["date_utc"])
    except (ValueError, TypeError, OverflowError, OSError):
        pass
    return item


def emails(store_dir: Path, query: str = "", view: str = "all", page: int = 1) -> dict:
    query = query.strip()[:200]
    view = view if view in VIEWS else "all"
    clauses, args = ["i.absent_since_run IS NULL"], []
    if query:
        pattern = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        clauses.append("""(i.subject LIKE ? ESCAPE '\\' OR i.from_addr LIKE ? ESCAPE '\\'
            OR EXISTS (SELECT 1 FROM parts p WHERE p.item_id=i.item_id AND p.filename LIKE ? ESCAPE '\\'))""")
        args.extend([pattern] * 3)
    if view in ("inbox", "sent"):
        clauses.append("""EXISTS (SELECT 1 FROM observations o JOIN json_each(o.labels) j
            WHERE o.item_id=i.item_id AND o.run_id=i.last_seen_run AND j.value=?)""")
        args.append(view.upper())
    elif view == "attachments":
        clauses.append("""EXISTS (SELECT 1 FROM parts p WHERE p.item_id=i.item_id
            AND p.filename IS NOT NULL AND p.filename != '')""")
    where = " AND ".join(clauses)
    with closing(connection(store_dir)) as db:
        count = db.execute(f"SELECT count(*) FROM items i WHERE {where}", args).fetchone()[0]
        pages = max(1, math.ceil(count / PAGE_SIZE))
        page = min(max(1, page), pages)
        rows = db.execute(f"""SELECT i.item_id,i.subject,i.from_addr,i.internal_date,i.date_utc,
            (SELECT o.labels FROM observations o WHERE o.item_id=i.item_id AND o.run_id=i.last_seen_run) labels,
            (SELECT count(*) FROM parts p WHERE p.item_id=i.item_id AND p.filename != '') attachment_count
            FROM items i WHERE {where} ORDER BY CAST(i.internal_date AS INTEGER) DESC,i.date_utc DESC,i.item_id
            LIMIT ? OFFSET ?""", [*args, PAGE_SIZE, (page - 1) * PAGE_SIZE]).fetchall()
    return {"rows": [_display(row) for row in rows], "count": count, "page": page, "pages": pages,
            "query": query, "view": view, "view_title": VIEWS[view]}


def email(store_dir: Path, item_id: str) -> dict | None:
    with closing(connection(store_dir)) as db:
        row = db.execute("""SELECT i.*,(SELECT o.labels FROM observations o
            WHERE o.item_id=i.item_id AND o.run_id=i.last_seen_run) labels FROM items i WHERE i.item_id=?""",
                         (item_id,)).fetchone()
        if row is None:
            return None
        item = _display(row)
        item["attachments"] = [dict(p) for p in db.execute("""SELECT filename,mime_type,size FROM parts
            WHERE item_id=? AND filename IS NOT NULL AND filename != '' ORDER BY part_id""", (item_id,))]
        return item
