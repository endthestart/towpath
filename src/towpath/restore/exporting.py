"""What the export writes for a restore."""

from contextlib import closing

from towpath import decisions as item_settings
from towpath.restore.checks import restorable_setting
from towpath.restore.common import MAIL_KEYS, TABLES, WRITER
from towpath.stores import open_store, store_path


def _rows(db, sql: str, args=()) -> list[dict]:
    try:
        return [dict(r) for r in db.execute(sql, args)]
    except Exception:  # noqa: BLE001 - a store from before a table: nothing of it to export
        return []


def package(store_dir) -> dict[str, list]:
    """The files the export adds under ``restore/``."""
    out = {name: [] for name in TABLES}
    if store_path(store_dir, "connections").exists():
        with closing(open_store(store_dir, "connections", WRITER)) as db:
            out["accounts"] = _rows(db, "SELECT source_id, provider, display_name FROM connections ORDER BY source_id")
    if store_path(store_dir, "decisions").exists():
        with closing(open_store(store_dir, "decisions", WRITER)) as db:
            mail_targets = {f"mail:{a['source_id']}" for a in out["accounts"]}
            for r in _rows(db, "SELECT * FROM settings ORDER BY target_id, key"):
                if restorable_setting(r["target_id"], r["key"], r["value"]) or (
                        r["target_id"] in mail_targets and r["key"] in MAIL_KEYS):
                    out["settings"].append(r)
            for table in ("dismissals", "collections", "questions"):
                out[table] = _rows(db, f"SELECT * FROM {table}")
            # members of reference sets only: a photo collection's accepted photos are never written to a file
            out["collection_items"] = _rows(db, "SELECT * FROM collection_items WHERE role = 'member'")
        items = [r for r in out["settings"] if r["key"] in item_settings.SETTINGS and r["target_id"].startswith("itm_")]
        if items and store_path(store_dir, "source").exists():
            with closing(open_store(store_dir, "source", WRITER)) as src:
                for r in items:  # a message's mark moves with the message, whose ID is made from its account's
                    row = src.execute("SELECT source_id, native_id FROM items WHERE item_id = ?",
                                      (r["target_id"],)).fetchone()
                    if row:
                        r["item"] = {"source_id": row[0], "native_id": row[1]}
    if store_path(store_dir, "maildecisions").exists():
        with closing(open_store(store_dir, "maildecisions", WRITER)) as db:
            for table in ("sender_marks", "thread_marks"):
                out[table] = _rows(db, f"SELECT * FROM {table}")
    return out
