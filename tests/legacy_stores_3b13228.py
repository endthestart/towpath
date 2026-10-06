# ruff: noqa: E501
"""Exact DDL of stores created by Towpath at commit 3b13228 (codex/local-email-ui), the base of PR #4.

Generated from that revision's towpath.stores by opening each store with its writer role and dumping
sqlite_master. Used to prove that today's upgrade procedure handles real older databases.
"""

BASE_COMMIT = "3b13228"
DDL = {
    "source": [
        "CREATE TABLE sources (\n  source_id TEXT PRIMARY KEY, kind TEXT NOT NULL, adapter TEXT NOT NULL, descriptor TEXT NOT NULL)",
        "CREATE TABLE runs (\n  seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT UNIQUE, source_id TEXT NOT NULL, kind TEXT NOT NULL,\n  started_at TEXT NOT NULL, finished_at TEXT, complete INTEGER NOT NULL DEFAULT 0,\n  cursor_before TEXT, cursor_after TEXT, termination TEXT, reason TEXT, items_processed INTEGER)",
        "CREATE TABLE sync_state (\n  source_id TEXT PRIMARY KEY, cursor TEXT, full_sync_history_id TEXT, full_sync_run TEXT, full_sync_phase TEXT, full_sync_page_token TEXT)",
        "CREATE TABLE full_sync_seen (\n  source_id TEXT NOT NULL, native_id TEXT NOT NULL, PRIMARY KEY (source_id, native_id))",
        "CREATE TABLE full_sync_listed (\n  source_id TEXT NOT NULL, native_id TEXT NOT NULL, PRIMARY KEY (source_id, native_id))",
        "CREATE TABLE items (\n  item_id TEXT PRIMARY KEY, source_id TEXT NOT NULL, native_id TEXT NOT NULL, thread_id TEXT,\n  rfc_message_id TEXT, subject TEXT, from_addr TEXT, date_header TEXT, date_utc TEXT, internal_date TEXT,\n  size_estimate INTEGER, first_seen_run TEXT NOT NULL, last_seen_run TEXT NOT NULL, absent_since_run TEXT,\n  structure_truncated INTEGER NOT NULL DEFAULT 0, inline_data_discarded INTEGER NOT NULL DEFAULT 0,\n  problems TEXT NOT NULL DEFAULT '[]', UNIQUE (source_id, native_id))",
        "CREATE TABLE parts (\n  item_id TEXT NOT NULL, part_id TEXT NOT NULL, depth INTEGER NOT NULL, mime_type TEXT, charset TEXT,\n  filename TEXT, disposition TEXT, size INTEGER, attachment_id TEXT,\n  sha256 TEXT, sha1_b64 TEXT, md5 TEXT, fetched_run TEXT, PRIMARY KEY (item_id, part_id))",
        "CREATE TABLE observations (\n  item_id TEXT NOT NULL, run_id TEXT NOT NULL, observed_at TEXT NOT NULL, labels TEXT NOT NULL,\n  PRIMARY KEY (item_id, run_id))",
        "CREATE TABLE cache (\n  sha256 TEXT PRIMARY KEY, bytes BLOB NOT NULL, cached_at TEXT NOT NULL)",
        "CREATE TABLE fetches (\n  request_id TEXT PRIMARY KEY, item_id TEXT NOT NULL, part_id TEXT NOT NULL, status TEXT NOT NULL,\n  detail TEXT, at TEXT NOT NULL)",
        "CREATE TABLE destination_entries (\n  source_id TEXT NOT NULL, path TEXT NOT NULL, algorithm TEXT NOT NULL, checksum TEXT NOT NULL,\n  size INTEGER, first_seen_run TEXT NOT NULL, absent_since_run TEXT, PRIMARY KEY (source_id, path))",
        "CREATE TABLE matches (\n  left_item TEXT NOT NULL, right_item TEXT NOT NULL, strength TEXT NOT NULL, basis TEXT NOT NULL,\n  run_id TEXT NOT NULL, PRIMARY KEY (left_item, right_item))",
        "CREATE TABLE destination_lookups (\n  source_id TEXT NOT NULL, algorithm TEXT NOT NULL, checksum TEXT NOT NULL, present INTEGER NOT NULL,\n  remote_id TEXT, checked_run TEXT NOT NULL, checked_at TEXT NOT NULL, PRIMARY KEY (source_id, algorithm, checksum))",
        "CREATE TABLE coverage (\n  run_id TEXT PRIMARY KEY, source_id TEXT NOT NULL, complete INTEGER NOT NULL, items_seen INTEGER NOT NULL,\n  items_new INTEGER NOT NULL, items_absent INTEGER NOT NULL, items_indexed_total INTEGER NOT NULL,\n  unparseable TEXT NOT NULL, termination TEXT, reason TEXT)",
    ],
    "queue": [
        "CREATE TABLE content_requests (\n  seq INTEGER PRIMARY KEY AUTOINCREMENT, item_id TEXT NOT NULL, part_id TEXT NOT NULL,\n  requested_by TEXT NOT NULL, priority TEXT NOT NULL, created_at TEXT NOT NULL,\n  UNIQUE (item_id, part_id))",
        "CREATE TABLE presence_requests (\n  seq INTEGER PRIMARY KEY AUTOINCREMENT, destination_id TEXT NOT NULL, algorithm TEXT NOT NULL,\n  checksum TEXT NOT NULL, requested_by TEXT NOT NULL, created_at TEXT NOT NULL,\n  UNIQUE (destination_id, algorithm, checksum))",
    ],
    "decisions": [
        "CREATE TABLE dismissals (\n  match_key TEXT PRIMARY KEY, proposal_id TEXT, reason TEXT, author TEXT NOT NULL, at TEXT NOT NULL)",
        "CREATE TABLE settings (\n  target_id TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, author TEXT NOT NULL, at TEXT NOT NULL,\n  PRIMARY KEY (target_id, key))",
        "CREATE TABLE model_grants (\n  endpoint_id TEXT NOT NULL, fingerprint TEXT NOT NULL, data_class TEXT NOT NULL, author TEXT NOT NULL,\n  at TEXT NOT NULL, PRIMARY KEY (endpoint_id, fingerprint, data_class))",
        "CREATE TABLE file_grants (\n  root_alias TEXT NOT NULL, feature TEXT NOT NULL, author TEXT NOT NULL, at TEXT NOT NULL,\n  PRIMARY KEY (root_alias, feature))",
        "CREATE TABLE decision_log (\n  seq INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, target_id TEXT NOT NULL, detail TEXT NOT NULL,\n  author TEXT NOT NULL, at TEXT NOT NULL)",
    ],
}
