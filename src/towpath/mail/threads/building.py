"""The owner's marks, who the owner is, and building conversations from the mail store."""

import json
import sqlite3
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone

from towpath.canonical import digest
from towpath.mail.threads.common import NOREPLY, OLD_DAYS, OWNER_WROTE_LAST_DAYS, REBUILD_ROWS, ROLE, STATES, now
from towpath.stores import open_store, store_path


def marks(store_dir, source_id: str, role: str = ROLE) -> dict[str, str]:
    if not store_path(store_dir, "maildecisions").exists():
        return {}
    try:
        with closing(open_store(store_dir, "maildecisions", role)) as db:
            return {r[0]: r[1] for r in db.execute("SELECT thread_key, state FROM thread_marks WHERE source_id = ?",
                                                    (source_id,))}
    except sqlite3.OperationalError:  # written by a version without marks; the web service adds the table
        return {}


def mark(store_dir, source_id: str, thread_key: str, state: str | None, author: str = "owner") -> None:
    """The owner's word on a conversation (web role). ``None`` returns it to Towpath's own judgement."""
    if state is not None and state not in STATES:
        raise ValueError("unknown state")
    with closing(open_store(store_dir, "maildecisions", "web")) as db:
        if state is None:
            db.execute("DELETE FROM thread_marks WHERE source_id = ? AND thread_key = ?", (source_id, thread_key))
        else:
            db.execute("INSERT OR REPLACE INTO thread_marks VALUES (?,?,?,?,?)",
                       (source_id, thread_key, state, author, now()))
        db.commit()


# -- who the owner is -------------------------------------------------------------------------------------------

def sent_mailboxes(store_dir, source_id: str, role: str = ROLE) -> set[str]:
    from towpath import connections

    c = connections.get(store_dir, source_id, role=role) or {}
    found = {f["name"] for f in c.get("folders") or () if (f.get("special") or "").lower() == "sent"}
    return found or {"Sent", "Sent Items", "Sent Messages"}


def owner(store_dir, source_id: str, mail, role: str = ROLE) -> dict:
    """The owner's addresses (the account's, and those the owner sends from) and the name they use."""
    from towpath import connections

    c = connections.get(store_dir, source_id, role=role) or {}
    username = ((c.get("settings") or {}).get("username") or "").lower()
    boxes = sorted(sent_mailboxes(store_dir, source_id, role))
    marks_ = ",".join("?" for _ in boxes)
    rows = mail.execute(f"SELECT from_addr, from_name FROM messages WHERE source_id = ? AND mailbox IN ({marks_}) "
                        f"AND from_addr IS NOT NULL", (source_id, *boxes)).fetchall()
    counts = Counter(r[0] for r in rows)
    addresses = {a for a, n in counts.items() if n >= 2} | ({username} if username else set())
    names = Counter(r[1] for r in rows if r[1] and r[0] in addresses)
    return {"addresses": addresses, "name": names.most_common(1)[0][0] if names else None,
            "address": username or (counts.most_common(1)[0][0] if counts else None), "sent": set(boxes)}


# -- building conversations -------------------------------------------------------------------------------------

def automated(m: dict) -> bool:
    if m.get("list_id") or m.get("list_unsubscribe"):
        return True
    if (m.get("auto_submitted") or "no").lower() != "no":
        return True
    if (m.get("precedence") or "").lower() in ("bulk", "list", "junk"):
        return True
    return bool(NOREPLY.search(m.get("from_addr") or ""))


def _messages(mail, source_id: str, thread_key: str) -> list[dict]:
    """The conversation's messages, oldest first, each once (a message filed in two folders counts once)."""
    rows = [dict(r) for r in mail.execute(
        "SELECT item_id, mailbox, message_id, from_addr, from_name, to_addrs, cc_addrs, subject, date_utc, text, "
        "list_id, list_unsubscribe, auto_submitted, precedence FROM messages WHERE source_id = ? AND thread_key = ? "
        "ORDER BY coalesce(date_utc, ''), item_id", (source_id, thread_key))]
    seen, out = set(), []
    for r in rows:
        key = r["message_id"] or r["item_id"]
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def _age_days(date: str | None) -> float:
    if not date:
        return 1e9
    return (datetime.now(timezone.utc) - datetime.fromisoformat(date)).total_seconds() / 86400


def summarize(source_id: str, thread_key: str, msgs: list[dict], me: dict) -> dict:
    """A conversation's facts and the state header rules give it."""
    def mine(m):
        return m["from_addr"] in me["addresses"] or m["mailbox"] in me["sent"]

    last = msgs[-1]
    others = [m for m in msgs if not mine(m)]
    owner_wrote = any(mine(m) for m in msgs)
    kind = "automated" if (not owner_wrote and others and automated(others[-1])) else "conversation"
    age = _age_days(last["date_utc"])
    last_mine = mine(last)
    if kind == "automated":
        state, reason = "fyi", "Automated or mailing-list mail."
    elif last_mine:
        state, reason = (("awaiting_others", "You wrote last.") if age <= OWNER_WROTE_LAST_DAYS else
                         ("done", "You wrote last, a while ago."))
    elif age > OLD_DAYS:
        state, reason = "done", "An old conversation."
    else:
        state, reason = (("needs_owner_reply", "They asked a question.") if "?" in (last["text"] or "") else
                         ("fyi", "No question in the latest message."))
    participants = sorted({m["from_addr"] for m in others if m["from_addr"]})[:10]
    return {"source_id": source_id, "thread_key": thread_key, "subject": msgs[0]["subject"] or last["subject"],
            "first_date": msgs[0]["date_utc"], "last_date": last["date_utc"], "last_item": last["item_id"],
            "last_from": last["from_addr"], "last_from_owner": int(last_mine), "messages": len(msgs),
            "participants": json.dumps(participants), "kind": kind, "state": state, "reason": reason,
            "input_digest": digest([[m["message_id"] or m["item_id"], m["date_utc"]] for m in msgs]),
            "model_eligible": kind == "conversation" and age <= OLD_DAYS}


def _save(db, t: dict, state_by: str = "rule", model: str | None = None) -> None:
    db.execute("""INSERT INTO threads (source_id, thread_key, subject, first_date, last_date, last_item, last_from,
                  last_from_owner, messages, participants, kind, state, state_by, reason, input_digest, model,
                  updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                  ON CONFLICT (source_id, thread_key) DO UPDATE SET subject=excluded.subject,
                  first_date=excluded.first_date, last_date=excluded.last_date, last_item=excluded.last_item,
                  last_from=excluded.last_from, last_from_owner=excluded.last_from_owner, messages=excluded.messages,
                  participants=excluded.participants, kind=excluded.kind, state=excluded.state,
                  state_by=excluded.state_by, reason=excluded.reason, input_digest=excluded.input_digest,
                  model=excluded.model, updated_at=excluded.updated_at""",
               (t["source_id"], t["thread_key"], t["subject"], t["first_date"], t["last_date"], t["last_item"],
                t["last_from"], t["last_from_owner"], t["messages"], t["participants"], t["kind"], t["state"],
                state_by, t["reason"], t["input_digest"], model, now()))


def _rebuild(intel, mail, store_dir, source_id: str, me: dict, deadline: float, clock) -> int:
    """Bring conversations up to date with messages read since the watermark. Returns conversations rebuilt."""
    row = intel.execute("SELECT watermark FROM progress WHERE source_id = ?", (source_id,)).fetchone()
    watermark = row[0] if row else 0
    rebuilt = 0
    while clock() < deadline:
        rows = mail.execute("SELECT rowid, thread_key FROM messages WHERE source_id = ? AND rowid > ? ORDER BY rowid "
                            "LIMIT ?", (source_id, watermark, REBUILD_ROWS)).fetchall()
        if not rows:
            break
        for key in dict.fromkeys(r[1] for r in rows):
            msgs = _messages(mail, source_id, key)
            if not msgs:
                continue
            t = summarize(source_id, key, msgs, me)
            old = intel.execute("SELECT input_digest, state_by FROM threads WHERE source_id = ? AND thread_key = ?",
                                (source_id, key)).fetchone()
            if old and old[0] == t["input_digest"] and old[1] != "rule":
                continue  # nothing new; keep the model's answer
            _save(intel, t)
            rebuilt += 1
        watermark = rows[-1][0]
        intel.execute("INSERT INTO progress (source_id, watermark, state, updated_at) VALUES (?,?,?,?) ON CONFLICT "
                      "(source_id) DO UPDATE SET watermark = excluded.watermark, updated_at = excluded.updated_at",
                      (source_id, watermark, "{}", now()))
        intel.commit()
    return rebuilt
