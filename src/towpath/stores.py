"""SQLite stores, one file per store, each with a single writing role.

| Store     | Writer  | Readers        |
| --------- | ------- | -------------- |
| source    | connect | worker, web    |
| queue     | worker, web (append only) | connect |
| derived   | worker  | web            |
| decisions | web     | worker, connect |

Readers open with ``mode=ro`` so the database itself refuses writes.
"""

import sqlite3
from pathlib import Path

WRITERS = {
    "source": {"connect"},
    "queue": {"worker", "web"},
    "derived": {"worker"},
    "decisions": {"web"},
}

SCHEMAS = {
    "source": """
CREATE TABLE IF NOT EXISTS sources (
  source_id TEXT PRIMARY KEY, kind TEXT NOT NULL, adapter TEXT NOT NULL, descriptor TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS runs (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT UNIQUE, source_id TEXT NOT NULL, kind TEXT NOT NULL,
  started_at TEXT NOT NULL, finished_at TEXT, complete INTEGER NOT NULL DEFAULT 0,
  cursor_before TEXT, cursor_after TEXT);
CREATE TABLE IF NOT EXISTS sync_state (
  source_id TEXT PRIMARY KEY, cursor TEXT, full_sync_history_id TEXT, full_sync_run TEXT);
CREATE TABLE IF NOT EXISTS full_sync_seen (
  source_id TEXT NOT NULL, native_id TEXT NOT NULL, PRIMARY KEY (source_id, native_id));
CREATE TABLE IF NOT EXISTS items (
  item_id TEXT PRIMARY KEY, source_id TEXT NOT NULL, native_id TEXT NOT NULL, thread_id TEXT,
  rfc_message_id TEXT, subject TEXT, from_addr TEXT, date_header TEXT, date_utc TEXT, internal_date TEXT,
  size_estimate INTEGER, first_seen_run TEXT NOT NULL, last_seen_run TEXT NOT NULL, absent_since_run TEXT,
  structure_truncated INTEGER NOT NULL DEFAULT 0, inline_data_discarded INTEGER NOT NULL DEFAULT 0,
  problems TEXT NOT NULL DEFAULT '[]', UNIQUE (source_id, native_id));
CREATE TABLE IF NOT EXISTS parts (
  item_id TEXT NOT NULL, part_id TEXT NOT NULL, depth INTEGER NOT NULL, mime_type TEXT, charset TEXT,
  filename TEXT, disposition TEXT, size INTEGER, attachment_id TEXT,
  sha256 TEXT, sha1_b64 TEXT, md5 TEXT, fetched_run TEXT, PRIMARY KEY (item_id, part_id));
CREATE TABLE IF NOT EXISTS observations (
  item_id TEXT NOT NULL, run_id TEXT NOT NULL, observed_at TEXT NOT NULL, labels TEXT NOT NULL,
  PRIMARY KEY (item_id, run_id));
CREATE TABLE IF NOT EXISTS cache (
  sha256 TEXT PRIMARY KEY, bytes BLOB NOT NULL, cached_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS fetches (
  request_id TEXT PRIMARY KEY, item_id TEXT NOT NULL, part_id TEXT NOT NULL, status TEXT NOT NULL,
  detail TEXT, at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS destination_entries (
  source_id TEXT NOT NULL, path TEXT NOT NULL, algorithm TEXT NOT NULL, checksum TEXT NOT NULL,
  size INTEGER, first_seen_run TEXT NOT NULL, absent_since_run TEXT, PRIMARY KEY (source_id, path));
CREATE TABLE IF NOT EXISTS matches (
  left_item TEXT NOT NULL, right_item TEXT NOT NULL, strength TEXT NOT NULL, basis TEXT NOT NULL,
  run_id TEXT NOT NULL, PRIMARY KEY (left_item, right_item));
CREATE TABLE IF NOT EXISTS coverage (
  run_id TEXT PRIMARY KEY, source_id TEXT NOT NULL, complete INTEGER NOT NULL, items_seen INTEGER NOT NULL,
  items_new INTEGER NOT NULL, items_absent INTEGER NOT NULL, items_indexed_total INTEGER NOT NULL,
  unparseable TEXT NOT NULL);
""",
    "queue": """
CREATE TABLE IF NOT EXISTS content_requests (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, item_id TEXT NOT NULL, part_id TEXT NOT NULL,
  requested_by TEXT NOT NULL, priority TEXT NOT NULL, created_at TEXT NOT NULL,
  UNIQUE (item_id, part_id));
""",
    "derived": """
CREATE TABLE IF NOT EXISTS scans (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, selector_id TEXT NOT NULL, at TEXT NOT NULL, candidates INTEGER,
  waiting INTEGER, present INTEGER, proposed INTEGER, dismissed INTEGER);
CREATE TABLE IF NOT EXISTS scan_matches (
  match_key TEXT PRIMARY KEY, selector_id TEXT NOT NULL, item_id TEXT NOT NULL, part_id TEXT NOT NULL,
  destination_id TEXT NOT NULL, status TEXT NOT NULL, dest_algorithm TEXT, dest_checksum TEXT,
  updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS proposals (
  proposal_id TEXT PRIMARY KEY, match_key TEXT NOT NULL, digest TEXT NOT NULL, body TEXT NOT NULL,
  state TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS model_input_queue (
  item_id TEXT NOT NULL, part_id TEXT NOT NULL, task TEXT NOT NULL, status TEXT NOT NULL,
  PRIMARY KEY (item_id, part_id, task));
""",
    "decisions": """
CREATE TABLE IF NOT EXISTS dismissals (
  match_key TEXT PRIMARY KEY, proposal_id TEXT, reason TEXT, author TEXT NOT NULL, at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS settings (
  target_id TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, author TEXT NOT NULL, at TEXT NOT NULL,
  PRIMARY KEY (target_id, key));
CREATE TABLE IF NOT EXISTS decision_log (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, target_id TEXT NOT NULL, detail TEXT NOT NULL,
  author TEXT NOT NULL, at TEXT NOT NULL);
""",
}


class RoleError(PermissionError):
    pass


def store_path(store_dir: Path, store: str) -> Path:
    return Path(store_dir) / f"{store}.db"


def open_store(store_dir: Path, store: str, role: str) -> sqlite3.Connection:
    """Open ``store`` for ``role``: read-write for its writers, read-only otherwise.

    A reader whose store does not exist yet gets an empty in-memory copy of the
    schema, so it never creates another role's file.
    """
    if store not in SCHEMAS:
        raise ValueError(f"unknown store {store!r}")
    path = store_path(store_dir, store)
    if role in WRITERS[store]:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path)
        conn.executescript(SCHEMAS[store])
    elif path.exists():
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    else:
        conn = sqlite3.connect(":memory:")
        conn.executescript(SCHEMAS[store])
        conn.execute("PRAGMA query_only = ON")
    conn.row_factory = sqlite3.Row
    return conn


def require_writer(store: str, role: str) -> None:
    if role not in WRITERS[store]:
        raise RoleError(f"role {role!r} may not write the {store} store")
