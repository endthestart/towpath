"""People: everyone the owner corresponds with, from mail management's senders and the owner's contacts.

A person is an email address mail management counts as a person (the owner wrote to them, they are in the owner's
contacts, the owner marked them, or the model judged so). Addresses are never merged into one person by Towpath; a
contact's several addresses are shown together because the contact card groups them.
"""

import json
from collections import Counter
from contextlib import closing

from towpath import dav_sync, life
from towpath.mail import FEATURE, senders, threads
from towpath.stores import open_store, store_path
from towpath.unified import grants


def _managed(store_dir) -> list[str]:
    return sorted(sid for sid, f in grants.granted(store_dir).items() if FEATURE in f)


def everyone(store_dir, limit: int = 500, role: str = "web") -> list[dict]:
    """People across managed accounts, most mail first."""
    names = dav_sync.names(store_dir, role)
    found: dict[str, dict] = {}
    for sid in _managed(store_dir):
        for s in senders.listing(store_dir, sid, "person", "messages", limit=10**6, role=role):
            p = found.setdefault(s["address"], {"address": s["address"], "name": names.get(s["address"]) or s["name"],
                                                "messages": 0, "first": s["first_date"], "last": s["last_date"],
                                                "accounts": []})
            p["messages"] += s["messages"]
            p["first"] = min(filter(None, [p["first"], s["first_date"]]), default=None)
            p["last"] = max(filter(None, [p["last"], s["last_date"]]), default=None)
            p["accounts"].append(sid)
    for address, name in names.items():
        found.setdefault(address, {"address": address, "name": name, "messages": 0, "first": None, "last": None,
                                   "accounts": []})
    return sorted(found.values(), key=lambda p: (-p["messages"], p["name"] or p["address"]))[:limit]


def person(store_dir, address: str, role: str = "web") -> dict:
    """One person: mail by year (both ways), recent conversations, and life events from mail with them."""
    address = address.lower()
    names = dav_sync.names(store_dir, role)
    by_year: Counter = Counter()
    sent_by_year: Counter = Counter()
    conversations = []
    refs = set()
    if store_path(store_dir, "mail").exists():
        with closing(open_store(store_dir, "mail", role)) as db:
            for sid in _managed(store_dir):
                sent = sorted(threads.sent_mailboxes(store_dir, sid, role))
                marks = ",".join("?" for _ in sent)
                for year, n in db.execute("SELECT substr(date_utc, 1, 4), count(DISTINCT coalesce(message_id, "
                                          "item_id)) FROM messages WHERE source_id = ? AND from_addr = ? GROUP BY 1",
                                          (sid, address)):
                    by_year[year] += n
                for year, to in db.execute(f"SELECT substr(date_utc, 1, 4), to_addrs FROM messages WHERE source_id = ? "
                                           f"AND mailbox IN ({marks})", (sid, *sent)):
                    if address in json.loads(to):
                        sent_by_year[year] += 1
                for r in db.execute("SELECT item_id, subject, date_utc, thread_key FROM messages WHERE source_id = ? "
                                    "AND from_addr = ? ORDER BY date_utc DESC LIMIT 200", (sid, address)):
                    refs.add(r[0])
                    if len(conversations) < 15 and r[3] not in {c["thread_key"] for c in conversations}:
                        conversations.append({"item_id": r[0], "subject": r[1], "date": r[2], "thread_key": r[3]})
    native = set()
    if refs:
        with closing(open_store(store_dir, "source", role)) as src:
            ids = sorted(refs)
            for start in range(0, len(ids), 900):
                chunk = ids[start:start + 900]
                native |= {f"{r[0]}:{r[1]}" for r in src.execute(
                    f"SELECT source_id, native_id FROM items WHERE item_id IN ({','.join('?' for _ in chunk)})", chunk)}
    events = [c for c in life.claims(store_dir, limit=10**6, role=role)
              if c.state != "rejected" and any(cit.ref in native for cit in c.citations)]
    chats = []  # days of messages (Apple Messages, Facebook) with someone of this name, from their exports
    name = names.get(address)
    if not name and store_path(store_dir, "mail").exists():  # the name they write under
        with closing(open_store(store_dir, "mail", role)) as db:
            row = db.execute("SELECT from_name FROM messages WHERE from_addr = ? AND coalesce(from_name, '') != '' "
                             "LIMIT 1", (address,)).fetchone()
        name = row[0] if row else None
    if name and store_path(store_dir, "source").exists():
        with closing(open_store(store_dir, "source", role)) as src:
            chats = [dict(r) for r in src.execute(
                "SELECT i.source_id, i.native_id, i.subject, i.date_utc FROM items i JOIN sources s USING (source_id) "
                "WHERE s.adapter IN ('imessage', 'facebook') AND lower(i.from_addr) = lower(?) AND "
                "i.absent_since_run IS NULL ORDER BY i.date_utc DESC LIMIT 20", (name,))]
    years = sorted(set(by_year) | set(sent_by_year), reverse=True)
    dated = [y for y in years if y]
    return {"address": address, "name": names.get(address), "years": [
        {"year": y, "received": by_year.get(y, 0), "sent": sent_by_year.get(y, 0)} for y in dated],
            "conversations": conversations, "events": events, "accounts": _managed(store_dir), "chats": chats,
            "received": sum(by_year.values()), "sent": sum(sent_by_year.values()),
            "span": (min(dated), max(dated)) if dated else None}
