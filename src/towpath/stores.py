"""SQLite stores, one file per store, each with a single writing role.

| Store     | Writer  | Readers        |
| --------- | ------- | -------------- |
| source    | connect | worker, web    |
| queue     | worker, web (append only) | connect |
| derived   | worker  | web            |
| decisions | web     | worker, connect |
| ledger    | worker  | web            |
| quota     | connect | connect        |
| files     | connect | worker, web    |
| connections | connect | worker, web  |
| mail      | connect | worker, act, web |
| mailplan  | worker  | act, web       |
| mailact   | act     | worker, web    |
| maildecisions | web | act, worker    |
| mailintel | worker  | web            |

Readers open with ``mode=ro`` so the database itself refuses writes.
"""

import sqlite3
from pathlib import Path

WRITERS = {
    "source": {"connect"},
    "queue": {"worker", "web"},
    "derived": {"worker"},
    "decisions": {"web"},
    "ledger": {"worker"},
    "quota": {"connect"},
    "files": {"connect"},
    "life": {"worker"},
    "connections": {"connect"},
    "mail": {"connect"},
    "mailplan": {"worker"},
    "mailact": {"act"},
    "maildecisions": {"web"},
    "mailintel": {"worker"},
}

# Columns and tables added after a store was first released. Applied on every
# writer open, idempotently, so existing local databases upgrade in place.
ADDED_COLUMNS = {
    "source": {
        "runs": {"termination": "TEXT", "reason": "TEXT", "items_processed": "INTEGER"},
        "coverage": {"termination": "TEXT", "reason": "TEXT"},
        "sync_state": {"full_sync_phase": "TEXT", "full_sync_page_token": "TEXT"},
    },
    "files": {
        "citations": {"source_stamp": "TEXT"},
    },
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
CREATE TABLE IF NOT EXISTS full_sync_listed (
  source_id TEXT NOT NULL, native_id TEXT NOT NULL, PRIMARY KEY (source_id, native_id));
CREATE TABLE IF NOT EXISTS items (
  item_id TEXT PRIMARY KEY, source_id TEXT NOT NULL, native_id TEXT NOT NULL, thread_id TEXT,
  rfc_message_id TEXT, subject TEXT, from_addr TEXT, date_header TEXT, date_utc TEXT, internal_date TEXT,
  size_estimate INTEGER, first_seen_run TEXT NOT NULL, last_seen_run TEXT NOT NULL, absent_since_run TEXT,
  structure_truncated INTEGER NOT NULL DEFAULT 0, inline_data_discarded INTEGER NOT NULL DEFAULT 0,
  problems TEXT NOT NULL DEFAULT '[]', UNIQUE (source_id, native_id));
-- Present and absent message counts for status pages without reading every row.
CREATE INDEX IF NOT EXISTS items_presence ON items (source_id, absent_since_run);
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
CREATE TABLE IF NOT EXISTS destination_lookups (
  source_id TEXT NOT NULL, algorithm TEXT NOT NULL, checksum TEXT NOT NULL, present INTEGER NOT NULL,
  remote_id TEXT, checked_run TEXT NOT NULL, checked_at TEXT NOT NULL, PRIMARY KEY (source_id, algorithm, checksum));
CREATE TABLE IF NOT EXISTS search_runs (
  request_seq INTEGER PRIMARY KEY, request_key TEXT NOT NULL, ran_at TEXT NOT NULL, response TEXT NOT NULL);
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
CREATE TABLE IF NOT EXISTS search_requests (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, request_key TEXT NOT NULL, filters TEXT NOT NULL,
  requested_by TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS presence_requests (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, destination_id TEXT NOT NULL, algorithm TEXT NOT NULL,
  checksum TEXT NOT NULL, requested_by TEXT NOT NULL, created_at TEXT NOT NULL,
  UNIQUE (destination_id, algorithm, checksum));
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
  endpoint_fingerprint TEXT, detail TEXT, PRIMARY KEY (item_id, part_id, task));
CREATE TABLE IF NOT EXISTS model_results (
  item_id TEXT NOT NULL, part_id TEXT NOT NULL, task TEXT NOT NULL, output TEXT NOT NULL, endpoint_id TEXT NOT NULL,
  endpoint_fingerprint TEXT NOT NULL, method TEXT NOT NULL, at TEXT NOT NULL, PRIMARY KEY (item_id, part_id, task));
""",
    "decisions": """
CREATE TABLE IF NOT EXISTS dismissals (
  match_key TEXT PRIMARY KEY, proposal_id TEXT, reason TEXT, author TEXT NOT NULL, at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS settings (
  target_id TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, author TEXT NOT NULL, at TEXT NOT NULL,
  PRIMARY KEY (target_id, key));
CREATE TABLE IF NOT EXISTS model_grants (
  endpoint_id TEXT NOT NULL, fingerprint TEXT NOT NULL, data_class TEXT NOT NULL, author TEXT NOT NULL,
  at TEXT NOT NULL, PRIMARY KEY (endpoint_id, fingerprint, data_class));
CREATE TABLE IF NOT EXISTS file_grants (
  root_alias TEXT NOT NULL, feature TEXT NOT NULL, author TEXT NOT NULL, at TEXT NOT NULL,
  PRIMARY KEY (root_alias, feature));
CREATE TABLE IF NOT EXISTS source_grants (
  source_id TEXT NOT NULL, feature TEXT NOT NULL, author TEXT NOT NULL, at TEXT NOT NULL,
  PRIMARY KEY (source_id, feature));
CREATE TABLE IF NOT EXISTS collections (
  collection_id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, kind TEXT NOT NULL, definition TEXT,
  author TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS collection_items (
  collection_id TEXT NOT NULL, ref TEXT NOT NULL, role TEXT NOT NULL, version TEXT, title TEXT, source_type TEXT,
  note TEXT, author TEXT NOT NULL, added_at TEXT NOT NULL, PRIMARY KEY (collection_id, ref, role));
CREATE TABLE IF NOT EXISTS decision_log (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, target_id TEXT NOT NULL, detail TEXT NOT NULL,
  author TEXT NOT NULL, at TEXT NOT NULL);
""",
    # Accounts added in the UI: non-secret settings, chosen folders, indexing state and progress. Secrets are
    # never stored here; they live in the connector's credentials folder (towpath.connections).
    "connections": """
CREATE TABLE IF NOT EXISTS connections (
  source_id TEXT PRIMARY KEY, provider TEXT NOT NULL, adapter TEXT NOT NULL, display_name TEXT NOT NULL,
  settings TEXT NOT NULL, folders TEXT, state TEXT NOT NULL, indexing TEXT NOT NULL, progress TEXT,
  last_error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS connection_events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, source_id TEXT NOT NULL, at TEXT NOT NULL, event TEXT NOT NULL,
  detail TEXT);
CREATE TABLE IF NOT EXISTS instance_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);
""",
    "quota": """
CREATE TABLE IF NOT EXISTS attempts (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, budget_id TEXT NOT NULL, account TEXT NOT NULL,
  method TEXT NOT NULL, units INTEGER NOT NULL, day TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS attempts_budget_at ON attempts (budget_id, at);
CREATE INDEX IF NOT EXISTS attempts_budget_day ON attempts (budget_id, day);
""",
    # Optional file discovery (towpath.discovery): rebuildable references, never file text.
    "files": """
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY, provider_id TEXT NOT NULL, kind TEXT NOT NULL, started_at TEXT NOT NULL,
  finished_at TEXT, termination TEXT, reason TEXT, items_seen INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS coverage (
  run_id TEXT PRIMARY KEY, provider_id TEXT NOT NULL, root_alias TEXT, complete INTEGER NOT NULL,
  scanned INTEGER NOT NULL, by_status TEXT NOT NULL, reason TEXT);
CREATE TABLE IF NOT EXISTS occurrences (
  occurrence_id TEXT PRIMARY KEY, provider_id TEXT NOT NULL, native_id TEXT NOT NULL, root_alias TEXT NOT NULL,
  rel_path TEXT NOT NULL, members TEXT NOT NULL, media_type TEXT, size INTEGER, dates TEXT NOT NULL,
  hashes TEXT NOT NULL, extraction TEXT NOT NULL, version TEXT, first_seen_run TEXT NOT NULL,
  last_seen_run TEXT NOT NULL, missing_since_run TEXT, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS occurrences_root ON occurrences (provider_id, root_alias);
-- Substring search over paths and member names at catalog scale (millions of files): a trigram index kept in
-- step with occurrences by triggers. Queries narrow with it, then apply the exact match to the candidates.
CREATE VIRTUAL TABLE IF NOT EXISTS occurrences_text USING fts5(text, tokenize='trigram');
CREATE TRIGGER IF NOT EXISTS occurrences_text_insert AFTER INSERT ON occurrences BEGIN
  INSERT INTO occurrences_text (rowid, text) VALUES (new.rowid, new.rel_path || ' ' || new.members);
END;
CREATE TRIGGER IF NOT EXISTS occurrences_text_update AFTER UPDATE OF rel_path, members ON occurrences BEGIN
  UPDATE occurrences_text SET text = new.rel_path || ' ' || new.members WHERE rowid = new.rowid;
END;
CREATE TRIGGER IF NOT EXISTS occurrences_text_delete AFTER DELETE ON occurrences BEGIN
  DELETE FROM occurrences_text WHERE rowid = old.rowid;
END;
-- Each root's present occurrences counted by extraction status, as [[status, count], ...]: rebuilt after every
-- import so that status pages never count millions of rows.
CREATE TABLE IF NOT EXISTS catalog_counts (
  provider_id TEXT NOT NULL, root_alias TEXT NOT NULL, counts TEXT NOT NULL, updated_at TEXT NOT NULL,
  PRIMARY KEY (provider_id, root_alias));
-- Space used under each folder of each root (top-level files only), measured from the catalog after each import.
CREATE TABLE IF NOT EXISTS space_folders (
  provider_id TEXT NOT NULL, root_alias TEXT NOT NULL, path TEXT NOT NULL, parent TEXT, files INTEGER NOT NULL,
  bytes INTEGER NOT NULL, PRIMARY KEY (provider_id, root_alias, path));
CREATE INDEX IF NOT EXISTS space_folders_children ON space_folders (provider_id, root_alias, parent, bytes);
-- Per root: totals, space by type, largest files and clutter (JSON), and the import run they describe.
CREATE TABLE IF NOT EXISTS space_summary (
  provider_id TEXT NOT NULL, root_alias TEXT NOT NULL, run_id TEXT NOT NULL, body TEXT NOT NULL,
  updated_at TEXT NOT NULL, PRIMARY KEY (provider_id, root_alias));
-- Files that are probably copies of each other (same name and size, across all roots), measured with the space.
CREATE TABLE IF NOT EXISTS space_duplicates (
  provider_id TEXT PRIMARY KEY, body TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS occurrence_versions (
  occurrence_id TEXT NOT NULL, version TEXT NOT NULL, first_seen_run TEXT NOT NULL,
  PRIMARY KEY (occurrence_id, version));
CREATE TABLE IF NOT EXISTS citations (
  citation_id TEXT PRIMARY KEY, occurrence_id TEXT NOT NULL, version TEXT, location TEXT NOT NULL,
  excerpt_sha256 TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS recoveries (
  recovery_id TEXT PRIMARY KEY, occurrence_id TEXT NOT NULL, version TEXT, path TEXT NOT NULL,
  sha256 TEXT NOT NULL, size INTEGER NOT NULL, created_at TEXT NOT NULL);
""",
    "life": """
-- Claims the life-story job proposed (towpath.claim/0 documents), each dated by its evidence. Rebuildable: the
-- owner's reviews live in the decisions store, keyed by claim ID, so they survive a re-run.
CREATE TABLE IF NOT EXISTS claims (
  claim_id TEXT PRIMARY KEY, body TEXT NOT NULL, earliest TEXT, latest TEXT, source_id TEXT, run_id TEXT NOT NULL,
  created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS claims_by_date ON claims (earliest, claim_id);
-- One proposal run over a slice of one source the owner chose; resumable from what it has already read.
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY, source_id TEXT NOT NULL, slice TEXT NOT NULL, state TEXT NOT NULL, progress TEXT,
  started_at TEXT NOT NULL, finished_at TEXT);
CREATE TABLE IF NOT EXISTS examined (
  run_id TEXT NOT NULL, item_id TEXT NOT NULL, outcome TEXT NOT NULL, at TEXT NOT NULL,
  PRIMARY KEY (run_id, item_id));
""",
    # Mail management (towpath.mail): what Towpath has read of each message it manages. Rebuildable from the
    # mailbox; written only by the connector, which holds the read-only connection.
    "mail": """
CREATE TABLE IF NOT EXISTS messages (
  item_id TEXT PRIMARY KEY, source_id TEXT NOT NULL, mailbox TEXT NOT NULL, uidvalidity INTEGER NOT NULL,
  uid INTEGER NOT NULL, message_id TEXT, in_reply_to TEXT, refs TEXT NOT NULL DEFAULT '[]', thread_key TEXT NOT NULL,
  from_name TEXT, from_addr TEXT, to_addrs TEXT NOT NULL DEFAULT '[]', cc_addrs TEXT NOT NULL DEFAULT '[]',
  reply_to TEXT, subject TEXT, date_utc TEXT, size INTEGER, flags TEXT NOT NULL DEFAULT '[]', list_id TEXT,
  list_unsubscribe TEXT, list_unsubscribe_post TEXT, auto_submitted TEXT, precedence TEXT,
  has_attachments INTEGER NOT NULL DEFAULT 0, text TEXT, text_truncated INTEGER NOT NULL DEFAULT 0,
  quoted_removed INTEGER NOT NULL DEFAULT 0, problems TEXT NOT NULL DEFAULT '[]', analyzed_at TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS messages_identity ON messages (source_id, mailbox, uidvalidity, uid);
CREATE INDEX IF NOT EXISTS messages_thread ON messages (source_id, thread_key);
CREATE INDEX IF NOT EXISTS messages_box ON messages (source_id, mailbox, date_utc);
CREATE INDEX IF NOT EXISTS messages_sender ON messages (source_id, from_addr);
CREATE INDEX IF NOT EXISTS messages_message_id ON messages (source_id, message_id);
-- Full-text search over what Towpath read of each message, kept in step by triggers; it stays searchable after
-- the message leaves the server. Filled for older stores by a worker job ("rebuild"), never at startup.
CREATE VIRTUAL TABLE IF NOT EXISTS messages_text USING fts5(subject, from_name, from_addr, text, content='messages',
  content_rowid='rowid', tokenize='unicode61 remove_diacritics 2');
CREATE TRIGGER IF NOT EXISTS messages_text_insert AFTER INSERT ON messages BEGIN
  INSERT INTO messages_text (rowid, subject, from_name, from_addr, text)
  VALUES (new.rowid, new.subject, new.from_name, new.from_addr, new.text);
END;
CREATE TRIGGER IF NOT EXISTS messages_text_delete AFTER DELETE ON messages BEGIN
  INSERT INTO messages_text (messages_text, rowid, subject, from_name, from_addr, text)
  VALUES ('delete', old.rowid, old.subject, old.from_name, old.from_addr, old.text);
END;
CREATE TRIGGER IF NOT EXISTS messages_text_update AFTER UPDATE OF subject, from_name, from_addr, text ON messages
WHEN old.subject IS NOT new.subject OR old.from_name IS NOT new.from_name OR old.from_addr IS NOT new.from_addr
  OR old.text IS NOT new.text BEGIN
  INSERT INTO messages_text (messages_text, rowid, subject, from_name, from_addr, text)
  VALUES ('delete', old.rowid, old.subject, old.from_name, old.from_addr, old.text);
  INSERT INTO messages_text (rowid, subject, from_name, from_addr, text)
  VALUES (new.rowid, new.subject, new.from_name, new.from_addr, new.text);
END;
-- Messages archived to a file before leaving the server: where each one is and the hash that was verified.
CREATE TABLE IF NOT EXISTS archived (
  item_id TEXT PRIMARY KEY, source_id TEXT NOT NULL, request_id TEXT NOT NULL, path TEXT NOT NULL,
  offset INTEGER NOT NULL, length INTEGER NOT NULL, sha256 TEXT NOT NULL, message_id TEXT, verified_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS archived_request ON archived (source_id, request_id);
CREATE TABLE IF NOT EXISTS archive_progress (
  request_id TEXT PRIMARY KEY, state TEXT NOT NULL, updated_at TEXT NOT NULL);
-- Per source: how far the analysis has read the source index (rowids), and its progress for the Mail page.
CREATE TABLE IF NOT EXISTS analysis (
  source_id TEXT PRIMARY KEY, high INTEGER NOT NULL, low INTEGER NOT NULL, progress TEXT NOT NULL,
  updated_at TEXT NOT NULL);
-- Per mailbox: the flag state Towpath last read, so later reads ask only for what changed (CONDSTORE).
CREATE TABLE IF NOT EXISTS mailbox_state (
  source_id TEXT NOT NULL, mailbox TEXT NOT NULL, uidvalidity INTEGER NOT NULL, modseq INTEGER,
  flags_read_at TEXT NOT NULL, PRIMARY KEY (source_id, mailbox));
-- Per source: what the new-mail watcher last saw (no message data).
CREATE TABLE IF NOT EXISTS watch (
  source_id TEXT PRIMARY KEY, state TEXT NOT NULL, detail TEXT, at TEXT NOT NULL);
""",
    # Mailbox changes Towpath's own features propose. Immutable: approvals live in the decisions store and
    # results in the mailact store, both keyed by proposal ID and digest.
    "mailplan": """
CREATE TABLE IF NOT EXISTS proposals (
  proposal_id TEXT PRIMARY KEY, source_id TEXT NOT NULL, body TEXT NOT NULL, digest TEXT NOT NULL,
  created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS proposals_source ON proposals (source_id, created_at);
""",
    # What the action runner did with each approved change: one receipt per request, never replaced.
    "mailact": """
CREATE TABLE IF NOT EXISTS receipts (
  key TEXT PRIMARY KEY, source_id TEXT NOT NULL, body TEXT NOT NULL, digest TEXT NOT NULL, outcome TEXT NOT NULL,
  detail TEXT, before TEXT, after TEXT, inverse TEXT, at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS receipts_source ON receipts (source_id, at);
""",
    # The owner's mailbox decisions: changes asked for directly on the Mail pages (approved by being asked for,
    # including Undo), and answers to proposals, each tied to the digest of exactly what was proposed.
    "maildecisions": """
CREATE TABLE IF NOT EXISTS requests (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, source_id TEXT NOT NULL, body TEXT NOT NULL, digest TEXT NOT NULL,
  author TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS answers (
  proposal_id TEXT PRIMARY KEY, digest TEXT NOT NULL, decision TEXT NOT NULL, author TEXT NOT NULL, at TEXT NOT NULL);
-- The owner's rules for an account: what to look for and what to do. Every change adds a revision; the version
-- counts changes to what the rule looks for or does, so proposals and automation are tied to exactly that.
CREATE TABLE IF NOT EXISTS rules (
  rule_id TEXT NOT NULL, revision INTEGER NOT NULL, source_id TEXT NOT NULL, version INTEGER NOT NULL,
  body TEXT NOT NULL, enabled INTEGER NOT NULL, mode TEXT NOT NULL, position INTEGER NOT NULL,
  deleted INTEGER NOT NULL DEFAULT 0, author TEXT NOT NULL, at TEXT NOT NULL, PRIMARY KEY (rule_id, revision));
-- Mail the owner asked to archive to a file (and, if asked, remove from the server once each copy is verified).
CREATE TABLE IF NOT EXISTS archive_requests (
  request_id TEXT PRIMARY KEY, source_id TEXT NOT NULL, item_ids TEXT NOT NULL, remove INTEGER NOT NULL,
  label TEXT NOT NULL, author TEXT NOT NULL, at TEXT NOT NULL);
-- The owner's category for a sender; it applies to all of that sender's mail and outranks rules and models.
CREATE TABLE IF NOT EXISTS sender_marks (
  source_id TEXT NOT NULL, address TEXT NOT NULL, category TEXT NOT NULL, author TEXT NOT NULL, at TEXT NOT NULL,
  PRIMARY KEY (source_id, address));
-- The owner's own word on a conversation's state (to reply, awaiting, FYI, done); it outranks rules and models.
CREATE TABLE IF NOT EXISTS thread_marks (
  source_id TEXT NOT NULL, thread_key TEXT NOT NULL, state TEXT NOT NULL, author TEXT NOT NULL, at TEXT NOT NULL,
  PRIMARY KEY (source_id, thread_key));
""",
    # What mail management concluded about conversations (rebuildable from the mail store and the models).
    "mailintel": """
CREATE TABLE IF NOT EXISTS threads (
  source_id TEXT NOT NULL, thread_key TEXT NOT NULL, subject TEXT, first_date TEXT, last_date TEXT,
  last_item TEXT NOT NULL, last_from TEXT, last_from_owner INTEGER NOT NULL, messages INTEGER NOT NULL,
  participants TEXT NOT NULL, kind TEXT NOT NULL, state TEXT NOT NULL, state_by TEXT NOT NULL, reason TEXT,
  input_digest TEXT NOT NULL, model TEXT, updated_at TEXT NOT NULL, PRIMARY KEY (source_id, thread_key));
CREATE INDEX IF NOT EXISTS threads_state ON threads (source_id, state, last_date);
CREATE TABLE IF NOT EXISTS drafts (
  source_id TEXT NOT NULL, thread_key TEXT NOT NULL, reply_to_item TEXT NOT NULL, input_digest TEXT NOT NULL,
  body TEXT NOT NULL, model TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (source_id, thread_key));
-- Everyone who writes to the owner, with totals from the mail store and a category (rules, the model, or the owner).
CREATE TABLE IF NOT EXISTS senders (
  source_id TEXT NOT NULL, address TEXT NOT NULL, name TEXT, domain TEXT, messages INTEGER NOT NULL,
  unread INTEGER NOT NULL, bytes INTEGER NOT NULL, first_date TEXT, last_date TEXT, list_id TEXT, unsubscribe TEXT,
  one_click INTEGER NOT NULL, owner_wrote INTEGER NOT NULL, inbox INTEGER NOT NULL, category TEXT NOT NULL,
  category_by TEXT NOT NULL, reason TEXT, input_digest TEXT NOT NULL, updated_at TEXT NOT NULL,
  PRIMARY KEY (source_id, address));
CREATE INDEX IF NOT EXISTS senders_category ON senders (source_id, category, messages);
-- Conversations already marked as important for the owner, and digests already proposed (so neither repeats).
CREATE TABLE IF NOT EXISTS alerts (
  source_id TEXT NOT NULL, thread_key TEXT NOT NULL, last_item TEXT NOT NULL, proposal_id TEXT NOT NULL,
  at TEXT NOT NULL, PRIMARY KEY (source_id, thread_key, last_item));
CREATE TABLE IF NOT EXISTS digests (
  source_id TEXT NOT NULL, period TEXT NOT NULL, proposal_id TEXT NOT NULL, at TEXT NOT NULL,
  PRIMARY KEY (source_id, period));
-- Per source: mail statistics (volume by week, response times, correspondents), measured by the worker.
CREATE TABLE IF NOT EXISTS stats (source_id TEXT PRIMARY KEY, body TEXT NOT NULL, updated_at TEXT NOT NULL);
-- Per source: where the mail's space goes (by year, mailbox, category, largest messages), measured by the worker.
CREATE TABLE IF NOT EXISTS space (source_id TEXT PRIMARY KEY, body TEXT NOT NULL, updated_at TEXT NOT NULL);
-- Which messages each rule version has looked at (rowid watermark), and what it decided for each.
CREATE TABLE IF NOT EXISTS rule_runs (
  source_id TEXT NOT NULL, rule_id TEXT NOT NULL, version INTEGER NOT NULL, watermark INTEGER NOT NULL,
  examined INTEGER NOT NULL, matched INTEGER NOT NULL, updated_at TEXT NOT NULL,
  PRIMARY KEY (source_id, rule_id, version));
CREATE TABLE IF NOT EXISTS rule_matches (
  source_id TEXT NOT NULL, rule_id TEXT NOT NULL, version INTEGER NOT NULL, item_id TEXT NOT NULL,
  matched INTEGER NOT NULL, by TEXT NOT NULL, reason TEXT, proposals TEXT NOT NULL, at TEXT NOT NULL,
  PRIMARY KEY (source_id, rule_id, version, item_id));
-- Per source: how far the conversation job has looked at the mail store (its rowids), and its progress.
CREATE TABLE IF NOT EXISTS progress (
  source_id TEXT PRIMARY KEY, watermark INTEGER NOT NULL, state TEXT NOT NULL, updated_at TEXT NOT NULL);
""",
    "ledger": """
CREATE TABLE IF NOT EXISTS capability_reports (
  endpoint_id TEXT NOT NULL, fingerprint TEXT NOT NULL, at TEXT NOT NULL, report TEXT NOT NULL,
  PRIMARY KEY (endpoint_id, fingerprint));
CREATE TABLE IF NOT EXISTS calls (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, task TEXT NOT NULL, endpoint_id TEXT,
  fingerprint TEXT, destination TEXT, model TEXT, served_model TEXT, prompt_version TEXT, input_sha256 TEXT,
  data_class TEXT, method TEXT, outcome TEXT NOT NULL, detail TEXT, usage TEXT);
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
        _add_columns(conn, store)
    elif path.exists():
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    else:
        conn = sqlite3.connect(":memory:")
        conn.executescript(SCHEMAS[store])
        _add_columns(conn, store)
        conn.execute("PRAGMA query_only = ON")
    conn.row_factory = sqlite3.Row
    return conn


CATALOG_COUNTS = ("SELECT provider_id, root_alias, json_extract(extraction, '$.status') AS status, count(*) AS n "
                  "FROM occurrences WHERE missing_since_run IS NULL")


def _add_columns(conn: sqlite3.Connection, store: str) -> None:
    for table, columns in ADDED_COLUMNS.get(store, {}).items():
        present = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        for name, kind in columns.items():
            if name not in present:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")
    if store == "files" and not conn.execute("SELECT 1 FROM occurrences_text LIMIT 1").fetchone():
        # A catalog written before the search index existed: index what it already holds, once.
        conn.execute("INSERT INTO occurrences_text (rowid, text) SELECT rowid, rel_path || ' ' || members "
                     "FROM occurrences")
    if store == "files" and not conn.execute("SELECT 1 FROM catalog_counts LIMIT 1").fetchone():
        # A catalog written before the counts existed: count what it already holds, once.
        conn.execute(f"INSERT INTO catalog_counts (provider_id, root_alias, counts, updated_at) "
                     f"SELECT provider_id, root_alias, json_group_array(json_array(status, n)), "
                     f"strftime('%Y-%m-%dT%H:%M:%SZ', 'now') FROM ({CATALOG_COUNTS} GROUP BY provider_id, root_alias, "
                     f"status) GROUP BY provider_id, root_alias")
    conn.commit()


def require_writer(store: str, role: str) -> None:
    if role not in WRITERS[store]:
        raise RoleError(f"role {role!r} may not write the {store} store")


# -- explicit upgrades of existing stores ---------------------------------------------------------------

UPGRADE_COMMAND = "towpath stores upgrade --store-dir <folder holding the .db files>"


class SchemaOutdated(RuntimeError):
    """An existing store predates tables or columns this version reads. Readers never migrate."""

    def __init__(self, reports: list[dict]):
        self.reports = reports
        names = ", ".join(f"{r['store']} ({', '.join(r['missing_tables'] + sorted(r['missing_columns']))})"
                          for r in reports)
        super().__init__(f"these stores need an upgrade before use: {names}. Run: {UPGRADE_COMMAND}")


def writer_role(store: str) -> str:
    """The role an upgrade opens ``store`` with: one of its own writers, chosen deterministically."""
    return sorted(WRITERS[store])[0]


def _expected(store: str) -> dict[str, set[str]]:
    reference = sqlite3.connect(":memory:")
    try:
        reference.executescript(SCHEMAS[store])
        _add_columns(reference, store)
        return _shape(reference)
    finally:
        reference.close()


def _shape(conn: sqlite3.Connection) -> dict[str, set[str]]:
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' "
                                         "AND name NOT LIKE 'sqlite_%'")]
    return {t: {row[1] for row in conn.execute(f"PRAGMA table_info({t})")} for t in tables}


def schema_status(store_dir: Path, names=None) -> list[dict]:
    """Read-only check of every store (or ``names``): which tables and columns it lacks. Writes nothing."""
    reports = []
    for store in names or SCHEMAS:
        if store not in SCHEMAS:
            raise ValueError(f"unknown store {store!r}")
        path = store_path(store_dir, store)
        report = {"store": store, "path": str(path), "exists": path.exists(), "writer_role": writer_role(store),
                  "missing_tables": [], "missing_columns": {}}
        if path.exists():
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            try:
                actual = _shape(conn)
            finally:
                conn.close()
            for table, columns in _expected(store).items():
                if table not in actual:
                    report["missing_tables"].append(table)
                elif columns - actual[table]:
                    report["missing_columns"][table] = sorted(columns - actual[table])
            report["missing_tables"].sort()
        report["current"] = not report["missing_tables"] and not report["missing_columns"]
        reports.append(report)
    return reports


def require_current(store_dir: Path, names) -> None:
    """Raise SchemaOutdated if any existing store in ``names`` lacks what this version reads."""
    outdated = [r for r in schema_status(store_dir, names) if not r["current"]]
    if outdated:
        raise SchemaOutdated(outdated)


def upgrade(store_dir: Path, names=None) -> list[dict]:
    """Bring existing stores up to date, each opened by its own writer role. Idempotent.

    Only ``CREATE TABLE IF NOT EXISTS`` and ``ALTER TABLE ... ADD COLUMN`` run, so no existing row changes.
    Stores that do not exist yet are left alone: their writer creates them when it first runs.
    """
    reports = []
    for before in schema_status(store_dir, names):
        report = {"store": before["store"], "writer_role": before["writer_role"], "exists": before["exists"],
                  "added_tables": [], "added_columns": {}}
        if before["exists"] and not before["current"]:
            open_store(store_dir, before["store"], before["writer_role"]).close()
            after = schema_status(store_dir, [before["store"]])[0]
            report["added_tables"] = sorted(set(before["missing_tables"]) - set(after["missing_tables"]))
            report["added_columns"] = {t: c for t, c in before["missing_columns"].items()
                                       if t not in after["missing_columns"]}
            before = after
        report["current"] = before["current"]
        reports.append(report)
    return reports
