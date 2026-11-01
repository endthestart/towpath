"""Saving what was read, keeping flags current, and the connector's slice."""

import json
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone

from towpath.mail import settings, text
from towpath.mail.analysis.common import (
    FLAGS_BATCH,
    FLAGS_EVERY_SECONDS,
    FULL_FLAGS_EVERY_SECONDS,
    RETRY_SECONDS,
    ROLE,
    SLICE_SECONDS,
    eligible,
    _items,
    now,
)
from towpath.mail.analysis.reading import _Reader, _read_batch
from towpath.stores import open_store, store_path

COLUMNS = ("item_id", "source_id", "mailbox", "uidvalidity", "uid", "message_id", "in_reply_to", "refs",
           "thread_key", "from_name", "from_addr", "to_addrs", "cc_addrs", "reply_to", "subject", "date_utc", "size",
           "flags", "list_id", "list_unsubscribe", "list_unsubscribe_post", "auto_submitted", "precedence",
           "has_attachments", "text", "text_truncated", "quoted_removed", "problems", "analyzed_at")
JSON_COLUMNS = {"refs", "to_addrs", "cc_addrs", "flags", "problems"}


def _save(db, record: dict) -> None:
    """Insert or update in place (an upsert, not REPLACE, so the row keeps its rowid and the search index's
    triggers see an update rather than a silent delete)."""
    values = [text.dumps(record[c]) if c in JSON_COLUMNS else record.get(c) for c in COLUMNS[:-1]] + [now()]
    values = [int(v) if isinstance(v, bool) else v for v in values]
    updates = ", ".join(f"{c} = excluded.{c}" for c in COLUMNS if c != "item_id")
    db.execute(f"INSERT INTO messages ({', '.join(COLUMNS)}) VALUES ({', '.join('?' for _ in COLUMNS)}) "
               f"ON CONFLICT (item_id) DO UPDATE SET {updates}", values)


def build_search(config) -> list[dict]:
    """Fill the search index once for a mail store written before it existed (a worker job, not at startup)."""
    out = []
    if not store_path(config.store_dir, "mail").exists():
        return out
    with closing(open_store(config.store_dir, "mail", ROLE)) as db:
        has_rows = db.execute("SELECT 1 FROM messages LIMIT 1").fetchone()
        indexed = db.execute("SELECT 1 FROM messages_text_docsize LIMIT 1").fetchone()
        if has_rows and not indexed:
            started = time.monotonic()
            db.execute("INSERT INTO messages_text (messages_text) VALUES ('rebuild')")
            db.commit()
            out.append({"step": "mail-search-index", "seconds": round(time.monotonic() - started, 1)})
    return out


def _refresh_flags(db, reader: _Reader, source_id: str, clock_now: datetime) -> int:
    """Bring stored flags up to date for each mailbox Towpath has read messages from. Returns rows changed."""
    changed = 0
    boxes = [r[0] for r in db.execute("SELECT DISTINCT mailbox FROM messages WHERE source_id = ?", (source_id,))]
    for mailbox in boxes:
        if mailbox not in reader.connector._mailboxes():
            continue
        state = db.execute("SELECT * FROM mailbox_state WHERE source_id = ? AND mailbox = ?",
                           (source_id, mailbox)).fetchone()
        last = datetime.fromisoformat(state["flags_read_at"]) if state else None
        every = FLAGS_EVERY_SECONDS if reader.condstore else FULL_FLAGS_EVERY_SECONDS
        if last is not None and clock_now - last < timedelta(seconds=every):
            continue
        info = reader.examine(mailbox)
        uidvalidity = info["uidvalidity"]
        same = state is not None and state["uidvalidity"] == uidvalidity
        known = [r[0] for r in db.execute("SELECT uid FROM messages WHERE source_id = ? AND mailbox = ? AND "
                                          "uidvalidity = ? ORDER BY uid", (source_id, mailbox, uidvalidity))]
        rows = {}
        if not (reader.condstore and same and state["modseq"] is not None and info["highestmodseq"] == state["modseq"]):
            since = (f"CHANGEDSINCE {state['modseq']}",) if reader.condstore and same and state["modseq"] else ()
            for start in range(0, len(known), FLAGS_BATCH):
                rows.update(reader.session.fetch(known[start:start + FLAGS_BATCH], ("FLAGS",), since))
        for uid, data in rows.items():
            flags = sorted(f.decode() if isinstance(f, bytes) else str(f) for f in data.get(b"FLAGS", ()))
            changed += db.execute("UPDATE messages SET flags = ? WHERE source_id = ? AND mailbox = ? AND "
                                  "uidvalidity = ? AND uid = ? AND flags != ?",
                                  (text.dumps(flags), source_id, mailbox, uidvalidity, uid, text.dumps(flags))).rowcount
        db.execute("INSERT OR REPLACE INTO mailbox_state (source_id, mailbox, uidvalidity, modseq, flags_read_at) "
                   "VALUES (?,?,?,?,?)", (source_id, mailbox, uidvalidity, info.get("highestmodseq"),
                                          clock_now.isoformat(timespec="seconds")))
    return changed


def _counts(src, db, source_id: str, state: dict) -> dict:
    present = src.execute("SELECT count(*) FROM items WHERE source_id = ? AND absent_since_run IS NULL",
                          (source_id,)).fetchone()[0]
    read = db.execute("SELECT count(*) FROM messages WHERE source_id = ?", (source_id,)).fetchone()[0]
    return {**state, "indexed": present, "read": read}


def analyze_pending(config, slice_seconds: float = SLICE_SECONDS, clock=time.monotonic) -> list[dict]:
    """One bounded slice for each managed account. Never raises for an account problem: the stop is recorded
    with a plain reason and tried again later."""
    from towpath.adapters import build_connector
    from towpath.adapters.errors import SyncStop
    from towpath.adapters.imap import _mapped_errors

    summaries = []
    for sid in eligible(config):
        if settings.paused(config.store_dir, sid):
            continue
        deadline = clock() + slice_seconds
        db = open_store(config.store_dir, "mail", ROLE)
        src = open_store(config.store_dir, "source", "worker")  # read-only: the index this job reads from
        connector = None
        try:
            row = db.execute("SELECT high, low, progress FROM analysis WHERE source_id = ?", (sid,)).fetchone()
            top = src.execute("SELECT max(rowid) FROM items WHERE source_id = ?", (sid,)).fetchone()[0]
            if top is None:
                continue  # not indexed yet
            if row is None:
                high, low, state = top, top + 1, {"state": "reading", "read_now": 0}
            else:
                high, low, state = row["high"], row["low"], json.loads(row["progress"])
            if state.get("retry_at") and now() < state["retry_at"]:
                continue
            state = {k: v for k, v in state.items() if k not in ("reason", "retry_at")}
            new = _items(src, sid, after=high, limit=1)
            backlog = low > 1 and _items(src, sid, before=low, limit=1)
            stale = state.get("flags_at") is None or (datetime.now(timezone.utc) - datetime.fromisoformat(
                state["flags_at"]) >= timedelta(seconds=FLAGS_EVERY_SECONDS))
            if not new and not backlog and not stale:
                continue
            read_now = 0
            try:
                with _mapped_errors():
                    connector = build_connector(config.sources[sid], config)
                    reader = _Reader(connector)
                    if stale:
                        clock_now = datetime.now(timezone.utc)
                        _refresh_flags(db, reader, sid, clock_now)
                        state["flags_at"] = clock_now.isoformat(timespec="seconds")
                        db.commit()
                    while clock() < deadline and not settings.paused(config.store_dir, sid):
                        rows = _items(src, sid, after=high)
                        direction = "new"
                        if not rows:
                            rows = _items(src, sid, before=low) if low > 1 else []
                            direction = "history"
                        if not rows:
                            break
                        for record in _read_batch(reader, sid, rows):
                            _save(db, record)
                            read_now += 1
                        if direction == "new":
                            high = max(r["rowid"] for r in rows)
                        else:
                            low = min(r["rowid"] for r in rows)
                        state = _counts(src, db, sid, {**state, "read_now": read_now})
                        db.execute("INSERT OR REPLACE INTO analysis (source_id, high, low, progress, updated_at) "
                                   "VALUES (?,?,?,?,?)", (sid, high, low, json.dumps(state), now()))
                        db.commit()
            except SyncStop as exc:
                retry = (datetime.now(timezone.utc) + timedelta(seconds=RETRY_SECONDS)).isoformat(timespec="seconds")
                state = {**state, "reason": f"The mail account stopped: {exc.reason}", "retry_at": retry}
            except Exception as exc:  # noqa: BLE001 - never stop the worker; keep message data out of the reason
                retry = (datetime.now(timezone.utc) + timedelta(seconds=RETRY_SECONDS)).isoformat(timespec="seconds")
                state = {**state, "reason": f"Reading mail stopped on an unexpected error ({type(exc).__name__}).",
                         "retry_at": retry}
            done = low <= (src.execute("SELECT min(rowid) FROM items WHERE source_id = ?", (sid,)).fetchone()[0] or 1)
            state = _counts(src, db, sid, {**state, "history_done": done})
            db.execute("INSERT OR REPLACE INTO analysis (source_id, high, low, progress, updated_at) "
                       "VALUES (?,?,?,?,?)", (sid, high, low, json.dumps(state), now()))
            db.commit()
            summaries.append({"source_id": sid, "step": "mail-reading", "read": read_now,
                              **({"stopped": state["reason"]} if state.get("reason") else {})})
        finally:
            db.close()
            src.close()
            if connector is not None:
                connector.close()
    return summaries
