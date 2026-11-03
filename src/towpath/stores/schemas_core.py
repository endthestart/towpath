"""Tables of the stores for sources, requests, decisions, connections, the file catalog and the life story."""

CORE_SCHEMAS = {
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
-- Mail searches the owner asked in plain English on an account's Search page.
CREATE TABLE IF NOT EXISTS mail_search_questions (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, source_id TEXT NOT NULL, text TEXT NOT NULL, author TEXT NOT NULL,
  created_at TEXT NOT NULL);
-- Rules the owner described in plain English on the Rules page, for the worker's model to draft.
CREATE TABLE IF NOT EXISTS rule_requests (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, source_id TEXT NOT NULL, text TEXT NOT NULL, author TEXT NOT NULL,
  created_at TEXT NOT NULL);
-- Plain-English searches the owner typed on the Search page, for the worker to turn into Towpath's query syntax.
CREATE TABLE IF NOT EXISTS search_questions (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL, author TEXT NOT NULL, created_at TEXT NOT NULL);
-- Plain-English photo requests the owner typed on the Photos page, for the worker to turn into filters.
CREATE TABLE IF NOT EXISTS photo_questions (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL, author TEXT NOT NULL, created_at TEXT NOT NULL);
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
CREATE TABLE IF NOT EXISTS mail_search_answers (
  seq INTEGER PRIMARY KEY, query TEXT NOT NULL, status TEXT NOT NULL, at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS rule_drafts (
  seq INTEGER PRIMARY KEY, body TEXT, status TEXT NOT NULL, at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS search_answers (
  seq INTEGER PRIMARY KEY, query TEXT NOT NULL, status TEXT NOT NULL, at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS photo_answers (
  seq INTEGER PRIMARY KEY, filters TEXT NOT NULL, status TEXT NOT NULL, at TEXT NOT NULL);
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
-- Questions the owner asked of their records (Ask); the worker's answers are in the life store.
CREATE TABLE IF NOT EXISTS questions (
  question_id TEXT PRIMARY KEY, text TEXT NOT NULL, author TEXT NOT NULL, at TEXT NOT NULL);
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
-- SHA-256 of files that share a name and size with another (duplicate candidates), to confirm exact copies.
CREATE TABLE IF NOT EXISTS content_hashes (
  provider_id TEXT NOT NULL, root_alias TEXT NOT NULL, rel_path TEXT NOT NULL, size INTEGER NOT NULL,
  sha256 TEXT, problem TEXT, hashed_at TEXT NOT NULL, PRIMARY KEY (provider_id, root_alias, rel_path));
-- Photo metadata read from image headers with ExifTool (camera, lens, when taken, where), for photo search.
CREATE TABLE IF NOT EXISTS photo_meta (
  occurrence_id TEXT PRIMARY KEY, taken TEXT, make TEXT, model TEXT, lens TEXT, lat REAL, lon REAL, width INTEGER,
  height INTEGER, problem TEXT, extracted_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS photo_meta_taken ON photo_meta (taken);
CREATE INDEX IF NOT EXISTS photo_meta_model ON photo_meta (model, taken);
CREATE TABLE IF NOT EXISTS photo_progress (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);
-- Files in an Apple Photos library that the library marks hidden or deleted (by its own database), and the
-- library's other files that aren't a visible original: never previewed, and left out of photo lists.
CREATE TABLE IF NOT EXISTS photo_hidden (occurrence_id TEXT PRIMARY KEY, why TEXT NOT NULL, at TEXT NOT NULL);
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
-- Groups of events proposed by the worker ("memories"): occasions and trips, people, life chapters. Rebuildable;
-- the groups the owner keeps are snapshots in the decisions store.
CREATE TABLE IF NOT EXISTS groups (
  group_id TEXT PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL, summary TEXT, earliest TEXT, latest TEXT,
  members TEXT NOT NULL, producer TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS groups_by_date ON groups (earliest, group_id);
-- Answers to the owner's questions, each sentence citing evidence (claims or messages) by number.
CREATE TABLE IF NOT EXISTS answers (
  question_id TEXT PRIMARY KEY, status TEXT NOT NULL, body TEXT NOT NULL, model TEXT, at TEXT NOT NULL);
-- What the grouping job has already looked at (a month of events, by the digest of its members).
CREATE TABLE IF NOT EXISTS group_runs (key TEXT PRIMARY KEY, digest TEXT NOT NULL, updated_at TEXT NOT NULL);
-- Copies of the evidence for events the owner kept, when the owner turned keeping it on (towpath.life_evidence).
CREATE TABLE IF NOT EXISTS kept_evidence (
  claim_id TEXT PRIMARY KEY, ref TEXT NOT NULL, path TEXT, sha256 TEXT, size INTEGER, status TEXT NOT NULL,
  at TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, next_try TEXT);
-- The owner's one bounded run: counts when it started and when it ended, no mail (towpath.life_propose.bounded_run).
CREATE TABLE IF NOT EXISTS run_once (
  started_at TEXT PRIMARY KEY, at_start TEXT NOT NULL, finished_at TEXT, at_end TEXT);
""",
}
