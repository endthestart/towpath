"""Tables of the thumbnail, contacts and calendar, and model ledger stores."""

OTHER_SCHEMAS = {
    # Contacts and calendar events read from CardDAV and CalDAV connections (rebuildable; references, not exports).
    "thumbs": """
-- Small previews of photos for the owner's pages: each photo's own embedded camera thumbnail, never for the private
-- export or a photo hidden in Apple Photos, never read by any model. A row with no image means none is made.
CREATE TABLE IF NOT EXISTS thumbs (
  occurrence_id TEXT PRIMARY KEY, jpeg BLOB, orientation INTEGER, why TEXT, at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS thumb_progress (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);
""",
    "dav": """
CREATE TABLE IF NOT EXISTS contacts (
  source_id TEXT NOT NULL, href TEXT NOT NULL, etag TEXT NOT NULL, uid TEXT, name TEXT, emails TEXT NOT NULL,
  phones TEXT NOT NULL, birthday TEXT, org TEXT, collection TEXT NOT NULL, updated_at TEXT NOT NULL,
  PRIMARY KEY (source_id, href));
-- Dates a contact card records besides the birthday (an anniversary, Apple's labelled dates). A row with no label
-- and no date marks a card read for its dates and found to have none.
CREATE TABLE IF NOT EXISTS contact_dates (
  source_id TEXT NOT NULL, href TEXT NOT NULL, label TEXT, date TEXT);
CREATE INDEX IF NOT EXISTS contact_dates_card ON contact_dates (source_id, href);
CREATE TABLE IF NOT EXISTS events (
  source_id TEXT NOT NULL, href TEXT NOT NULL, n INTEGER NOT NULL, etag TEXT NOT NULL, uid TEXT, summary TEXT,
  start TEXT, end TEXT, all_day INTEGER NOT NULL, tz TEXT, location TEXT, organizer TEXT, attendees TEXT NOT NULL,
  recurring INTEGER NOT NULL, description TEXT, collection TEXT NOT NULL, updated_at TEXT NOT NULL,
  PRIMARY KEY (source_id, href, n));
CREATE INDEX IF NOT EXISTS events_start ON events (source_id, start);
CREATE TABLE IF NOT EXISTS collections (
  source_id TEXT NOT NULL, href TEXT NOT NULL, kind TEXT NOT NULL, name TEXT, ctag TEXT, items INTEGER,
  synced_at TEXT, PRIMARY KEY (source_id, href));
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
