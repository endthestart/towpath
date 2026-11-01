"""Limits, which accounts are managed, and how far reading has got."""

import json
from contextlib import closing
from datetime import datetime, timezone

from towpath.mail import settings, text
from towpath.stores import open_store, store_path

ROLE = "connect"
SLICE_SECONDS = 60
BATCH = 50
TEXT_BYTES = 65_536  # read at most this much of a text part
HEADER_ITEM = f"BODY.PEEK[HEADER.FIELDS ({' '.join(text.HEADER_FIELDS)})]"
FLAGS_EVERY_SECONDS = 30  # with CONDSTORE: ask for changed flags at most this often per mailbox
FULL_FLAGS_EVERY_SECONDS = 3600  # without it: read every flag at most this often
FLAGS_BATCH = 1000  # UIDs per flag read
RETRY_SECONDS = 15 * 60


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def eligible(config) -> list[str]:
    """Managed accounts that this version can read: IMAP connections (Gmail joins over IMAP later)."""
    return sorted(sid for sid in settings.managed(config.store_dir)
                  if sid in config.sources and config.sources[sid].adapter == "imap")


def progress(store_dir, source_id: str, role: str = "web") -> dict | None:
    if not store_path(store_dir, "mail").exists():
        return None
    with closing(open_store(store_dir, "mail", role)) as db:
        row = db.execute("SELECT progress, updated_at FROM analysis WHERE source_id = ?", (source_id,)).fetchone()
    return {**json.loads(row[0]), "updated_at": row[1]} if row else None


def _items(src, source_id: str, after: int | None = None, before: int | None = None, limit: int = BATCH):
    if after is not None:
        return src.execute("SELECT rowid, item_id, native_id, absent_since_run FROM items WHERE source_id = ? "
                           "AND rowid > ? ORDER BY rowid LIMIT ?", (source_id, after, limit)).fetchall()
    return src.execute("SELECT rowid, item_id, native_id, absent_since_run FROM items WHERE source_id = ? "
                       "AND rowid < ? ORDER BY rowid DESC LIMIT ?", (source_id, before, limit)).fetchall()
