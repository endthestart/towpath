"""Reading messages from the server through the read-only session."""

from towpath.mail import text
from towpath.mail.analysis.common import HEADER_ITEM, TEXT_BYTES


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
