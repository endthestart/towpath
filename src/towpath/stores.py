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


def _add_columns(conn: sqlite3.Connection, store: str) -> None:
    for table, columns in ADDED_COLUMNS.get(store, {}).items():
        present = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        for name, kind in columns.items():
            if name not in present:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")
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
