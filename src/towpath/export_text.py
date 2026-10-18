"""The text of conversations from exports (Apple Messages, Facebook) kept for Search all.

Exports live in the read-only library, which only the connector can read; this job copies each conversation day's
text into the mail store's ``export_text`` (with a full-text index) so the web service can search it, as it searches
mail text that mail management has read. Bounded slices; each item once; items gone from the export are dropped.
"""

import time
from contextlib import closing
from datetime import datetime, timezone

from towpath.stores import open_store, store_path

ROLE = "connect"
ADAPTERS = ("facebook", "imessage")
SLICE_SECONDS = 30
BATCH = 500


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def index_pending(config, slice_seconds: float = SLICE_SECONDS, clock=time.monotonic) -> list[dict]:
    from towpath.adapters import build_connector

    sources = [s for s in config.sources.values() if s.adapter in ADAPTERS]
    if not sources or not store_path(config.store_dir, "source").exists():
        return []
    deadline = clock() + slice_seconds
    out = []
    with closing(open_store(config.store_dir, "source", ROLE)) as src, \
            closing(open_store(config.store_dir, "mail", ROLE)) as mail:
        for source in sources:
            present = {r[0]: r[1] for r in src.execute(
                "SELECT item_id, native_id FROM items WHERE source_id = ? AND absent_since_run IS NULL", (source.id,))}
            have = {r[0] for r in mail.execute("SELECT item_id FROM export_text WHERE source_id = ?", (source.id,))}
            gone = sorted(have - set(present))
            for start in range(0, len(gone), 900):
                chunk = gone[start:start + 900]
                mail.execute(f"DELETE FROM export_text WHERE item_id IN ({','.join('?' for _ in chunk)})", chunk)
            todo = [i for i in present if i not in have][:BATCH]
            if not todo and not gone:
                continue
            connector = build_connector(source, config)
            added = 0
            try:
                for item_id in todo:
                    if clock() >= deadline:
                        break
                    try:
                        text = connector.fetch(present[item_id], "1").decode("utf-8", "replace")
                    except Exception:  # noqa: BLE001 - an item the export no longer has: tried again next time
                        continue
                    mail.execute("INSERT OR REPLACE INTO export_text (item_id, source_id, text, at) VALUES (?,?,?,?)",
                                 (item_id, source.id, text, now()))
                    added += 1
                mail.commit()
            finally:
                connector.close()
            out.append({"source_id": source.id, "step": "export-text", "added": added, "dropped": len(gone)})
    return out


def search(store_dir, source_id: str, words: list[str], limit: int = 500, role: str = "web") -> list[str]:
    """Items of an export whose text has every word."""
    if not words or not store_path(store_dir, "mail").exists():
        return []
    match = " ".join('"' + w.replace('"', "") + '"' for w in words if w)
    with closing(open_store(store_dir, "mail", role)) as db:
        try:
            return [r[0] for r in db.execute(
                "SELECT e.item_id FROM export_text_index i JOIN export_text e ON e.rowid = i.rowid WHERE "
                "export_text_index MATCH ? AND e.source_id = ? LIMIT ?", (match, source_id, limit))]
        except Exception:  # noqa: BLE001 - an older store without the table, or a query FTS can't read
            return []
