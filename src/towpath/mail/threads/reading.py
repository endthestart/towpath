"""Reading conversations for the pages, and saving a draft as a mail message."""

import json
import uuid
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import format_datetime, formataddr

from towpath.mail.threads.building import marks, _messages, owner
from towpath.stores import open_store, store_path


def counts(store_dir, source_id: str, role: str = "web") -> dict[str, int]:
    if not store_path(store_dir, "mailintel").exists():
        return {}
    owner_marks = marks(store_dir, source_id, role)
    out = Counter()
    with closing(open_store(store_dir, "mailintel", role)) as db:
        for key, state in db.execute("SELECT thread_key, state FROM threads WHERE source_id = ? AND kind = "
                                     "'conversation'", (source_id,)):
            out[owner_marks.get(key, state)] += 1
    return dict(out)


def listing(store_dir, source_id: str, state: str, limit: int = 100, before: str | None = None,
            role: str = "web") -> list[dict]:
    """Conversations whose state (with the owner's marks applied) is ``state``, newest first."""
    if not store_path(store_dir, "mailintel").exists():
        return []
    owner_marks = marks(store_dir, source_id, role)
    marked_here = [k for k, s in owner_marks.items() if s == state]
    out = []
    with closing(open_store(store_dir, "mailintel", role)) as db:
        cursor = db.execute(
            "SELECT t.*, d.body AS draft, d.input_digest = t.input_digest AS draft_current FROM threads t LEFT JOIN "
            "drafts d USING (source_id, thread_key) WHERE t.source_id = ? AND t.kind = 'conversation' AND "
            "coalesce(t.last_date, '') < ? AND (t.state = ? OR t.thread_key IN (SELECT value FROM json_each(?))) "
            "ORDER BY t.last_date DESC", (source_id, before or "9999", state, json.dumps(marked_here)))
        for r in cursor:
            row = dict(r)
            row["state"] = owner_marks.get(row["thread_key"], row["state"])
            if row["state"] != state:
                continue
            row["marked"] = row["thread_key"] in owner_marks
            row["participants"] = json.loads(row["participants"])
            out.append(row)
            if len(out) >= limit:
                break
    return out


def thread(store_dir, source_id: str, thread_key: str, role: str = "web") -> dict | None:
    if not store_path(store_dir, "mailintel").exists():
        return None
    with closing(open_store(store_dir, "mailintel", role)) as db:
        row = db.execute("SELECT t.*, d.body AS draft, d.reply_to_item, d.input_digest = t.input_digest AS "
                         "draft_current FROM threads t LEFT JOIN drafts d USING (source_id, thread_key) WHERE "
                         "t.source_id = ? AND t.thread_key = ?", (source_id, thread_key)).fetchone()
    if row is None:
        return None
    out = dict(row)
    owner_marks = marks(store_dir, source_id, role)
    out["state"] = owner_marks.get(thread_key, out["state"])
    out["marked"] = thread_key in owner_marks
    return out


def draft_message(store_dir, source_id: str, thread_key: str, body: str, role: str = "web") -> str:
    """The reply as a complete message for the Drafts folder: addressed to whoever wrote last, threaded with
    In-Reply-To and References, with a new Message-ID so Undo can find it."""
    with closing(open_store(store_dir, "mail", role)) as mail:
        msgs = _messages(mail, source_id, thread_key)
        me = owner(store_dir, source_id, mail, role)
        mine = bool(msgs) and (msgs[-1]["from_addr"] in me["addresses"] or msgs[-1]["mailbox"] in me["sent"])
        # A reply answers whoever wrote last; a follow-up (the owner wrote last) goes after the owner's own message,
        # to whoever they wrote to.
        last = msgs[-1] if mine else next((m for m in reversed(msgs) if m["from_addr"] not in me["addresses"]),
                                          msgs[-1])
        detail = mail.execute("SELECT reply_to, refs FROM messages WHERE item_id = ?", (last["item_id"],)).fetchone()
    if not me["address"]:
        raise ValueError("Towpath doesn't know this account's address yet")
    m = EmailMessage()
    m["From"] = formataddr((me["name"] or "", me["address"]))
    if mine:
        m["To"] = ", ".join(a for a in json.loads(last["to_addrs"] or "[]") if a not in me["addresses"])
    else:
        m["To"] = (detail["reply_to"] if detail and detail["reply_to"] else last["from_addr"]) or ""
    subject = last["subject"] or ""
    m["Subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}".strip()
    m["Date"] = format_datetime(datetime.now(timezone.utc))
    domain = me["address"].split("@")[-1]
    m["Message-ID"] = f"<towpath-{uuid.uuid4().hex}@{domain}>"
    if last["message_id"]:
        refs = json.loads(detail["refs"]) if detail and detail["refs"] else []
        m["In-Reply-To"] = last["message_id"]
        m["References"] = " ".join([*refs, last["message_id"]][-20:])
    m.set_content(body.strip() + "\n")
    return m.as_string()
