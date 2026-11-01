"""Tables of the mail management stores."""

MAIL_SCHEMAS = {
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
CREATE INDEX IF NOT EXISTS messages_from_name ON messages (lower(from_name));
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
-- The text of conversations from exports (Apple Messages, Facebook), for Search all; written by the connector,
-- which alone can read the exports.
CREATE TABLE IF NOT EXISTS export_text (
  item_id TEXT PRIMARY KEY, source_id TEXT NOT NULL, text TEXT NOT NULL, at TEXT NOT NULL);
CREATE VIRTUAL TABLE IF NOT EXISTS export_text_index USING fts5(text, content='export_text', content_rowid='rowid');
CREATE TRIGGER IF NOT EXISTS export_text_insert AFTER INSERT ON export_text BEGIN
  INSERT INTO export_text_index (rowid, text) VALUES (new.rowid, new.text);
END;
CREATE TRIGGER IF NOT EXISTS export_text_delete AFTER DELETE ON export_text BEGIN
  INSERT INTO export_text_index (export_text_index, rowid, text) VALUES ('delete', old.rowid, old.text);
END;
-- Attachments filed into Towpath's data folder (documents only): planned while filing is a dry run, then filed,
-- each copy read back and checked against the hash of what the server sent.
CREATE TABLE IF NOT EXISTS filed (
  source_id TEXT NOT NULL, message_key TEXT NOT NULL, part_id TEXT NOT NULL, item_id TEXT NOT NULL, filename TEXT,
  path TEXT NOT NULL, size INTEGER, sha256 TEXT, status TEXT NOT NULL, detail TEXT, at TEXT NOT NULL,
  PRIMARY KEY (source_id, message_key, part_id));
CREATE INDEX IF NOT EXISTS filed_status ON filed (source_id, status, at);
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
-- Follow-up reminders: the owner's last message in a conversation nobody answered gets the label "Follow-up";
-- when someone answers, the label is taken off again.
CREATE TABLE IF NOT EXISTS follow_ups (
  source_id TEXT NOT NULL, thread_key TEXT NOT NULL, last_item TEXT NOT NULL, proposal_id TEXT NOT NULL,
  at TEXT NOT NULL, cleared_proposal TEXT, PRIMARY KEY (source_id, thread_key, last_item));
-- Reply labels: the latest message of a conversation waiting on the owner, or on someone else, carries "To-reply" or
-- "Awaiting-reply" in the mailbox; when the conversation moves on, the label moves or comes off.
CREATE TABLE IF NOT EXISTS reply_labels (
  source_id TEXT NOT NULL, thread_key TEXT NOT NULL, item TEXT NOT NULL, label TEXT NOT NULL,
  proposal_id TEXT NOT NULL, at TEXT NOT NULL, cleared_proposal TEXT,
  PRIMARY KEY (source_id, thread_key, item, label));
CREATE TABLE IF NOT EXISTS digests (
  source_id TEXT NOT NULL, period TEXT NOT NULL, proposal_id TEXT NOT NULL, at TEXT NOT NULL,
  PRIMARY KEY (source_id, period));
-- Per source: mail statistics (volume by week, response times, correspondents), measured by the worker.
CREATE TABLE IF NOT EXISTS stats (source_id TEXT PRIMARY KEY, body TEXT NOT NULL, updated_at TEXT NOT NULL);
-- Purchases read from receipts and order confirmations by the model: one row per message looked at (purchase = 0
-- for a message that turned out not to be one).
CREATE TABLE IF NOT EXISTS purchases (
  source_id TEXT NOT NULL, item_id TEXT NOT NULL, purchase INTEGER NOT NULL, merchant TEXT, day TEXT, total REAL,
  currency TEXT, items TEXT NOT NULL DEFAULT '[]', order_no TEXT, model TEXT, at TEXT NOT NULL,
  PRIMARY KEY (source_id, item_id));
CREATE INDEX IF NOT EXISTS purchases_day ON purchases (source_id, purchase, day);
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
}
