"""People: everyone the owner corresponds with, from mail management's senders and the owner's contacts.

A person is an email address mail management counts as a person (the owner wrote to them, they are in the owner's
contacts, the owner marked them, or the model judged so). Towpath never merges addresses by itself: it suggests
addresses that write under the same name, and the owner joins them into one person (or separates them again).
The joins are the owner's decisions (decisions store); a joined address's page is its person's page.
"""

import json
from collections import Counter
from contextlib import closing

from towpath import dav_sync, life
from towpath.mail import FEATURE, senders, threads
from towpath.stores import open_store, store_path
from towpath.unified import grants


JOIN_KEY = "person"


def _target(address: str) -> str:
    return f"address:{address}"


def valid_address(address: str) -> str:
    address = (address or "").strip().lower()
    if "@" not in address or len(address) > 320 or any(c.isspace() for c in address):
        raise ValueError("a person is joined by email address")
    return address


def joins(store_dir, role: str = "web") -> dict[str, str]:
    """Each joined address and the address of the person it belongs to."""
    if not store_path(store_dir, "decisions").exists():
        return {}
    with closing(open_store(store_dir, "decisions", role)) as db:
        return {t.split(":", 1)[1]: json.loads(v) for t, v in db.execute(
            "SELECT target_id, value FROM settings WHERE key = ? AND target_id LIKE 'address:%'", (JOIN_KEY,))}


def _resolve(found: dict[str, str], address: str) -> str:
    """Follow joins to the person's own address; a loop (only a damaged store could hold one) stops where it began."""
    seen = [address]
    while address in found and found[address] not in seen:
        address = found[address]
        seen.append(address)
    return address if address not in found else seen[0]


def primary(store_dir, address: str, role: str = "web") -> str:
    return _resolve(joins(store_dir, role), address.lower())


def addresses(store_dir, address: str, role: str = "web") -> list[str]:
    """A person's addresses: their own first, then those the owner joined to them."""
    found = joins(store_dir, role)
    main = _resolve(found, address.lower())
    return [main] + sorted(a for a in found if a != main and _resolve(found, a) == main)


def known(store_dir, address: str, role: str = "web") -> bool:
    """Whether an address is someone the owner corresponds with (in managed mail or in contacts)."""
    if address in dav_sync.names(store_dir, role):
        return True
    managed = _managed(store_dir)
    if not managed or not store_path(store_dir, "mail").exists():
        return False
    with closing(open_store(store_dir, "mail", role)) as db:
        return db.execute(f"SELECT 1 FROM messages WHERE from_addr = ? AND source_id IN "
                          f"({','.join('?' for _ in managed)}) LIMIT 1", (address, *managed)).fetchone() is not None


def join(store_dir, person: str, other: str, author: str = "owner") -> None:
    """Join ``other`` (and anyone already joined to it) to ``person``."""
    from towpath.decisions import now

    person, other = valid_address(person), valid_address(other)
    found = joins(store_dir)
    person = _resolve(found, person)
    if _resolve(found, other) == person:
        raise ValueError("that is already this person")
    if not known(store_dir, other):
        raise ValueError("Towpath doesn't know that address")
    moved = [other] + [a for a in found if a != other and _resolve(found, a) == other]
    with closing(open_store(store_dir, "decisions", "web")) as db:
        for a in moved:
            db.execute("INSERT OR REPLACE INTO settings (target_id, key, value, author, at) VALUES (?,?,?,?,?)",
                       (_target(a), JOIN_KEY, json.dumps(person), author, now()))
        db.execute("INSERT INTO decision_log (kind, target_id, detail, author, at) VALUES ('person-join', ?, ?, ?, ?)",
                   (_target(other), json.dumps({"person": person, "moved": moved}), author, now()))
        db.commit()


def separate(store_dir, address: str, author: str = "owner") -> None:
    from towpath.decisions import now

    address = valid_address(address)
    if address not in joins(store_dir):
        raise ValueError("that address isn't joined to anyone")
    with closing(open_store(store_dir, "decisions", "web")) as db:
        db.execute("DELETE FROM settings WHERE target_id = ? AND key = ?", (_target(address), JOIN_KEY))
        db.execute("INSERT INTO decision_log (kind, target_id, detail, author, at) VALUES ('person-separate', ?, ?, "
                   "?, ?)", (_target(address), "{}", author, now()))
        db.commit()


def _name_of(store_dir, address: str, names: dict, role: str) -> str | None:
    if names.get(address):
        return names[address]
    if not store_path(store_dir, "mail").exists():
        return None
    with closing(open_store(store_dir, "mail", role)) as db:
        row = db.execute("SELECT from_name FROM messages WHERE from_addr = ? AND coalesce(from_name, '') != '' "
                         "GROUP BY from_name ORDER BY count(*) DESC LIMIT 1", (address,)).fetchone()
    return row[0] if row else None


def suggestions(store_dir, address: str, role: str = "web", limit: int = 5) -> list[dict]:
    """Other addresses that may be the same person: the same full name in the owner's contacts or in the From
    line of their mail. Only suggested; nothing is joined until the owner says so."""
    names = dav_sync.names(store_dir, role)
    mine = addresses(store_dir, address, role)
    name = _name_of(store_dir, mine[0], names, role)
    if not name or len(name.split()) < 2 or "@" in name:  # a first name alone is too common to go on
        return []
    found = joins(store_dir, role)
    out = {a: "the same name in your contacts" for a, n in names.items()
           if n and n.strip().lower() == name.strip().lower()}
    managed = _managed(store_dir)
    if managed and store_path(store_dir, "mail").exists():
        with closing(open_store(store_dir, "mail", role)) as db:
            for (a,) in db.execute(f"SELECT DISTINCT from_addr FROM messages WHERE lower(from_name) = lower(?) AND "
                                   f"from_addr IS NOT NULL AND source_id IN ({','.join('?' for _ in managed)}) "
                                   f"LIMIT 50", (name, *managed)):
                out.setdefault(a.lower(), "writes under the same name")
    # never an address already joined to someone: joining it here would quietly take it from them
    return [{"address": a, "why": why} for a, why in sorted(out.items()) if a not in mine and a not in found and
            not any(_resolve(found, b) == a for b in found)][:limit]


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
    for address, main in joins(store_dir, role).items():  # the owner's joins: one row per person
        joined = found.pop(address, None)
        if joined is None:
            continue
        p = found.setdefault(main, {**joined, "address": main, "messages": 0, "accounts": []})
        p["messages"] += joined["messages"]
        p["first"] = min(filter(None, [p["first"], joined["first"]]), default=None)
        p["last"] = max(filter(None, [p["last"], joined["last"]]), default=None)
        p["accounts"] = sorted(set(p["accounts"]) | set(joined["accounts"]))
        p["name"] = p["name"] or joined["name"]
        p.setdefault("also", []).append(address)
    return sorted(found.values(), key=lambda p: (-p["messages"], p["name"] or p["address"]))[:limit]


def person(store_dir, address: str, role: str = "web") -> dict:
    """One person (with every address the owner joined to them): mail by year (both ways), recent conversations,
    and life events from mail with them."""
    group = addresses(store_dir, address, role)
    address = group[0]
    names = dav_sync.names(store_dir, role)
    marks_in = ",".join("?" for _ in group)
    by_year: Counter = Counter()
    sent_by_year: Counter = Counter()
    conversations = []
    refs = set()
    if store_path(store_dir, "mail").exists():
        with closing(open_store(store_dir, "mail", role)) as db:
            for sid in _managed(store_dir):
                sent = sorted(threads.sent_mailboxes(store_dir, sid, role))
                marks = ",".join("?" for _ in sent)
                for year, n in db.execute(f"SELECT substr(date_utc, 1, 4), count(DISTINCT coalesce(message_id, "
                                          f"item_id)) FROM messages WHERE source_id = ? AND from_addr IN ({marks_in}) "
                                          f"GROUP BY 1", (sid, *group)):
                    by_year[year] += n
                for year, to in db.execute(f"SELECT substr(date_utc, 1, 4), to_addrs FROM messages WHERE source_id = ? "
                                           f"AND mailbox IN ({marks})", (sid, *sent)):
                    if set(group) & set(json.loads(to)):
                        sent_by_year[year] += 1
                for r in db.execute(f"SELECT item_id, subject, date_utc, thread_key FROM messages WHERE source_id = ? "
                                    f"AND from_addr IN ({marks_in}) ORDER BY date_utc DESC LIMIT 200", (sid, *group)):
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
    chat_names = sorted({n.lower() for n in (_name_of(store_dir, a, names, role) for a in group)
                         if n and len(n.split()) >= 2})  # a first name alone would match strangers
    if chat_names and store_path(store_dir, "source").exists():  # the names they write under
        with closing(open_store(store_dir, "source", role)) as src:
            chats = [dict(r) for r in src.execute(
                f"SELECT i.source_id, i.native_id, i.subject, i.date_utc FROM items i JOIN sources s USING (source_id) "
                f"WHERE s.adapter IN ('imessage', 'facebook') AND lower(i.from_addr) IN "
                f"({','.join('?' for _ in chat_names)}) AND i.absent_since_run IS NULL ORDER BY i.date_utc DESC "
                f"LIMIT 20", chat_names)]
    years = sorted(set(by_year) | set(sent_by_year), reverse=True)
    dated = [y for y in years if y]
    return {"address": address, "also": group[1:], "name": next(filter(None, (names.get(a) for a in group)), None),
            "years": [
        {"year": y, "received": by_year.get(y, 0), "sent": sent_by_year.get(y, 0)} for y in dated],
            "conversations": conversations, "events": events, "accounts": _managed(store_dir), "chats": chats,
            "received": sum(by_year.values()), "sent": sum(sent_by_year.values()),
            "span": (min(dated), max(dated)) if dated else None}
