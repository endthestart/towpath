"""Reading managed accounts' mail into the mail store: headers, flags, thread and text, for every message.

Runs in the connector, through the account's read-only IMAP connection (EXAMINE and BODY.PEEK only, so nothing is
marked read). Each worker poll reads one bounded slice per account: first messages indexed since the last slice,
then the rest of the account's history, newest indexed first. Progress is the range of the source index already
read (rowids, which only a VACUUM would renumber, and Towpath never vacuums), so a stop at any point resumes where it
left off, and nothing is read at startup.

Flags change without new mail (a message read on the phone, a change Towpath made). Each slice asks every managed
mailbox for the flags that changed since the last read (CONDSTORE), or, on a server without it, reads every flag at
most hourly.
"""

import json
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone

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


class _Reader:
    """One slice's use of the read-only session: examined mailboxes are remembered, capabilities asked once."""

    def __init__(self, connector):
        self.connector = connector
        self.session = connector.session
        caps = {c.upper() for c in self.session.capabilities()}
        self.objectid = "OBJECTID" in caps
        self.gmail = "X-GM-EXT-1" in caps
        self.condstore = "CONDSTORE" in caps and self.session.enable("CONDSTORE")
        self.current: str | None = None
        self.info: dict = {}

    def examine(self, mailbox: str) -> dict:
        if self.current != mailbox:
            self.info = self.session.examine(mailbox)
            self.current = mailbox
        return self.info


def _key(data: dict, prefix: bytes):
    return next((v for k, v in data.items() if isinstance(k, bytes) and k.startswith(prefix)), None)


def _read_batch(reader: _Reader, source_id: str, rows) -> list[dict]:
    """Records for the batch's present messages, grouped by mailbox. Messages already gone are skipped."""
    from towpath.adapters.imap import flatten_structure
    from towpath.unified.contracts import ContractError, parse_imap_native

    by_box: dict[tuple[str, int], list[tuple[int, str]]] = {}
    for row in rows:
        if row["absent_since_run"]:
            continue
        try:
            mailbox, uidvalidity, uid = parse_imap_native(row["native_id"])
        except ContractError:
            continue
        by_box.setdefault((mailbox, uidvalidity), []).append((uid, row["item_id"]))
    records = []
    items = ("FLAGS", "RFC822.SIZE", "BODYSTRUCTURE", HEADER_ITEM) + (("THREADID",) if reader.objectid else ()) + (
        ("X-GM-THRID",) if reader.gmail else ())
    for (mailbox, uidvalidity), wanted in by_box.items():
        if mailbox not in reader.connector._mailboxes():
            continue
        if reader.examine(mailbox)["uidvalidity"] != uidvalidity:
            continue  # the mailbox was rebuilt: indexing lists it afresh, and those messages are read then
        uids = [u for u, _ in wanted]
        meta = reader.session.fetch(uids, items)
        texts: dict[str, list[int]] = {}
        parts_by_uid = {}
        for uid, _ in wanted:
            if uid not in meta:
                continue
            try:
                parts = flatten_structure(meta[uid].get(b"BODYSTRUCTURE"))
            except (IndexError, TypeError, ValueError):
                parts = []
            parts_by_uid[uid] = parts
            part = text.text_part(parts)
            if part is not None:
                texts.setdefault(part["part_id"], []).append(uid)
        bodies: dict[int, tuple[bytes, dict]] = {}
        for part_id, part_uids in texts.items():
            got = reader.session.fetch(part_uids, (f"BODY.PEEK[{part_id}]<0.{TEXT_BYTES}>",))
            for uid in part_uids:
                raw = _key(got.get(uid, {}), f"BODY[{part_id}]".encode())
                if raw is not None:
                    bodies[uid] = (raw, next(p for p in parts_by_uid[uid] if p["part_id"] == part_id))
        for uid, item_id in wanted:
            if uid not in meta:
                continue  # expunged since indexing
            try:
                records.append(_record(source_id, mailbox, uidvalidity, uid, item_id, meta[uid], parts_by_uid,
                                       bodies))
            except (TypeError, ValueError, LookupError, UnicodeError, IndexError) as exc:
                records.append(_unreadable(source_id, mailbox, uidvalidity, uid, item_id, meta[uid], exc))
    return records


def _unreadable(source_id, mailbox, uidvalidity, uid, item_id, data, exc) -> dict:
    """A message whose headers or text couldn't be made sense of: kept with its identity, size and flags, and the
    problem named (its type only), so one odd message never stops reading."""
    return {"item_id": item_id, "source_id": source_id, "mailbox": mailbox, "uidvalidity": uidvalidity, "uid": uid,
            "thread_key": f"i:{item_id}", "message_id": None, "in_reply_to": None, "refs": [], "from_name": None,
            "from_addr": None, "to_addrs": [], "cc_addrs": [], "reply_to": None, "subject": None, "date_utc": None,
            "size": data.get(b"RFC822.SIZE"), "list_id": None, "list_unsubscribe": None,
            "list_unsubscribe_post": None, "auto_submitted": None, "precedence": None,
            "flags": sorted(f.decode() if isinstance(f, bytes) else str(f) for f in data.get(b"FLAGS", ())),
            "has_attachments": False, "text": None, "text_truncated": False, "quoted_removed": False,
            "problems": [f"unreadable ({type(exc).__name__})"]}


def _record(source_id, mailbox, uidvalidity, uid, item_id, data, parts_by_uid, bodies) -> dict:
    """One message's record from its fetched headers, flags, structure and text."""
    fields = text.headers(_key(data, b"BODY[HEADER.FIELDS") or b"")
    thread = data.get(b"THREADID")
    if isinstance(thread, (tuple, list)):
        thread = thread[0] if thread else None
    thread = thread.decode() if isinstance(thread, bytes) else thread
    if thread is None and data.get(b"X-GM-THRID") is not None:  # Gmail's own thread ID
        thread = f"gm{data[b'X-GM-THRID']}"
    body_text, truncated, removed = None, False, False
    if uid in bodies:
        raw, part = bodies[uid]
        body_text, truncated, removed = text.message_text(raw, part, part["size"] > TEXT_BYTES)
    return {
        **fields, "item_id": item_id, "source_id": source_id, "mailbox": mailbox,
        "uidvalidity": uidvalidity, "uid": uid, "thread_key": text.thread_key(fields, thread, item_id),
        "size": data.get(b"RFC822.SIZE"),
        "flags": sorted(f.decode() if isinstance(f, bytes) else str(f) for f in data.get(b"FLAGS", ())),
        "has_attachments": any(p["disposition"] == "attachment" for p in parts_by_uid.get(uid, ())),
        "text": body_text, "text_truncated": truncated, "quoted_removed": removed}


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
