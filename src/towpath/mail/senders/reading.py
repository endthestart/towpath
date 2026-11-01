"""Reading senders and categories for the pages and for rules."""

from contextlib import closing

from towpath.mail.senders.common import ACCOUNT_SUBJECT, CALENDAR_SUBJECT, CATEGORIES, marks
from towpath.stores import open_store, store_path


def message_category(message: dict, sender_category: str | None) -> str:
    """A message's category: account-security and calendar mail recognised from the message, else its sender's."""
    subject = message.get("subject") or ""
    if ACCOUNT_SUBJECT.search(subject) and not message.get("list_id"):
        return "account"
    if CALENDAR_SUBJECT.search(subject):
        return "calendar"
    return sender_category or "other"


def categories(store_dir, source_id: str, role: str = "web") -> dict[str, str]:
    """Every sender's category with the owner's marks applied."""
    if not store_path(store_dir, "mailintel").exists():
        return {}
    with closing(open_store(store_dir, "mailintel", role)) as db:
        found = {r[0]: r[1] for r in db.execute("SELECT address, category FROM senders WHERE source_id = ?",
                                                (source_id,))}
    found.update(marks(store_dir, source_id, role))
    return found


def listing(store_dir, source_id: str, category: str | None = None, order: str = "messages", limit: int = 200,
            offset: int = 0, role: str = "web") -> list[dict]:
    if not store_path(store_dir, "mailintel").exists():
        return []
    owner_marks = marks(store_dir, source_id, role)
    orders = {"messages": "messages DESC", "recent": "last_date DESC", "space": "bytes DESC",
              "unread": "unread DESC"}
    with closing(open_store(store_dir, "mailintel", role)) as db:
        rows = [dict(r) for r in db.execute(f"SELECT * FROM senders WHERE source_id = ? ORDER BY "
                                            f"{orders.get(order, orders['messages'])}", (source_id,))]
    out = []
    for r in rows:
        r["marked"] = r["address"] in owner_marks
        r["category"] = owner_marks.get(r["address"], r["category"])
        r["label"] = CATEGORIES.get(r["category"], r["category"])
        if category is None or r["category"] == category:
            out.append(r)
    return out[offset:offset + limit]


def accounts(store_dir, source_id: str, role: str = "web") -> list[dict]:
    """Services the owner has an account with, from sign-in, security and password mail, by sender domain: how
    many such messages, the first and latest, and the latest subject. Most recent first."""
    if not store_path(store_dir, "mail").exists():
        return []
    cats = categories(store_dir, source_id, role)
    by_domain: dict[str, dict] = {}
    with closing(open_store(store_dir, "mail", role)) as db:
        for m in db.execute(
                "SELECT subject, from_addr, from_name, list_id, date_utc FROM messages WHERE source_id = ? AND ("
                "lower(subject) LIKE '%code%' OR lower(subject) LIKE '%sign%' OR lower(subject) LIKE '%log%in%' OR "
                "lower(subject) LIKE '%password%' OR lower(subject) LIKE '%verif%' OR lower(subject) LIKE '%secur%' OR "
                "lower(subject) LIKE '%factor%' OR lower(subject) LIKE '%device%' OR lower(subject) LIKE '%confirm%')",
                (source_id,)):
            m = dict(m)
            if not m["from_addr"] or message_category(m, cats.get(m["from_addr"])) != "account":
                continue
            domain = m["from_addr"].rpartition("@")[2]
            d = by_domain.setdefault(domain, {"domain": domain, "name": m["from_name"], "messages": 0,
                                              "first": m["date_utc"], "last": m["date_utc"], "subject": m["subject"]})
            d["messages"] += 1
            if (m["date_utc"] or "") < (d["first"] or "9999"):
                d["first"] = m["date_utc"]
            if (m["date_utc"] or "") > (d["last"] or ""):
                d["last"], d["subject"], d["name"] = m["date_utc"], m["subject"], m["from_name"] or d["name"]
    return sorted(by_domain.values(), key=lambda d: d["last"] or "", reverse=True)


def inbox(store_dir, source_id: str, role: str = "web") -> dict:
    """What is in the inbox now, by category: how many messages and how many unread, most first."""
    if not store_path(store_dir, "mail").exists():
        return {"total": 0, "unread": 0, "categories": []}
    cats = categories(store_dir, source_id, role)
    counts: dict[str, list] = {}
    with closing(open_store(store_dir, "mail", role)) as db:
        for m in db.execute("SELECT subject, from_addr, list_id, flags FROM messages WHERE source_id = ? AND "
                            "mailbox IN ('INBOX', 'Inbox', 'inbox')", (source_id,)):
            c = message_category(dict(m), cats.get(m["from_addr"]))
            got = counts.setdefault(c, [0, 0])
            got[0] += 1
            got[1] += "\\Seen" not in m["flags"]
    rows = sorted(({"category": c, "label": CATEGORIES.get(c, c), "messages": n, "unread": u}
                   for c, (n, u) in counts.items()), key=lambda r: -r["messages"])
    return {"total": sum(r["messages"] for r in rows), "unread": sum(r["unread"] for r in rows), "categories": rows}


def totals(store_dir, source_id: str, role: str = "web") -> list[dict]:
    """Senders and messages per category, with the owner's marks applied."""
    counts: dict[str, dict] = {}
    for r in listing(store_dir, source_id, limit=10**9, role=role):
        c = counts.setdefault(r["category"], {"category": r["category"], "label": r["label"], "senders": 0,
                                              "messages": 0, "bytes": 0})
        c["senders"] += 1
        c["messages"] += r["messages"]
        c["bytes"] += r["bytes"]
    return sorted(counts.values(), key=lambda c: -c["messages"])
