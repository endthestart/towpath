"""Rebuilding each sender's totals from the mail store, and measuring the space they take."""

import json
from contextlib import closing

from towpath.canonical import digest
from towpath.mail.senders.common import SHARED_DOMAINS, marks, now
from towpath.mail.senders.reading import totals
from towpath.stores import open_store, store_path


def _owner_wrote(mail, source_id: str, sent: set[str]) -> set[str]:
    marks_ = ",".join("?" for _ in sent)
    out = set()
    for to, cc in mail.execute(f"SELECT to_addrs, cc_addrs FROM messages WHERE source_id = ? AND mailbox IN "
                               f"({marks_})", (source_id, *sorted(sent))):
        out.update(json.loads(to))
        out.update(json.loads(cc))
    return out


def rebuild(intel, mail, source_id: str, me: dict, store_dir=None) -> int:
    """Recompute every sender's totals from the mail store, keeping categories that still fit. Returns senders."""
    from towpath.mail.threads import NOREPLY

    from towpath import dav_sync

    wrote = _owner_wrote(mail, source_id, me["sent"])
    known = dav_sync.names(store_dir)  # names and known people from the owner's contacts, if connected
    sent = sorted(me["sent"])
    marks_ = ",".join("?" for _ in sent)
    rows = mail.execute(  # each message once, though Gmail files it under All Mail and the Inbox
        f"""SELECT from_addr, max(from_name), count(*), sum(unread), sum(size), min(date_utc), max(date_utc),
                   max(list_id), max(list_unsubscribe), max(one_click), sum(in_inbox)
            FROM (SELECT from_addr, max(from_name) AS from_name, max(flags NOT LIKE '%\\Seen%') AS unread,
                         max(coalesce(size, 0)) AS size, max(date_utc) AS date_utc, max(list_id) AS list_id,
                         max(list_unsubscribe) AS list_unsubscribe,
                         max(list_unsubscribe_post IS NOT NULL) AS one_click, max(upper(mailbox) = 'INBOX') AS in_inbox
                  FROM messages WHERE source_id = ? AND from_addr IS NOT NULL AND mailbox NOT IN ({marks_})
                  GROUP BY coalesce(message_id, item_id))
            GROUP BY from_addr""", (source_id, *sent)).fetchall()
    old = {r[0]: r for r in intel.execute("SELECT address, category, category_by, reason, input_digest FROM senders "
                                          "WHERE source_id = ?", (source_id,))}
    # Learned from the owner: a category they gave one sender holds for others at the same domain (not for
    # personal mail domains, where one person says nothing about the next).
    learned: dict[str, str] = {}
    for address, category in marks(store_dir, source_id).items() if store_dir else ():
        domain = address.rpartition("@")[2]
        if domain and domain not in SHARED_DOMAINS and category != "person":
            learned.setdefault(domain, category)
    count = 0
    for (addr, name, n, unread, size, first, last, list_id, unsub, one_click, inbox) in rows:
        if addr in me["addresses"]:
            continue
        owner_wrote = addr in wrote
        key = digest([bool(list_id or unsub), owner_wrote, min(n, 3)])
        if owner_wrote:
            category, by, reason = "person", "rule", "You have written to them."
        elif addr in known:
            category, by, reason = "person", "rule", "In your contacts."
        elif addr.rpartition("@")[2] in learned:
            category, by = learned[addr.rpartition("@")[2]], "learned"
            reason = f"You chose this for another sender at {addr.rpartition('@')[2]}."
        elif NOREPLY.search(addr) and not (list_id or unsub):
            category, by, reason = "notification", "rule", "An automated address."
        elif addr in old and old[addr][4] == key and old[addr][2] in ("model", "rule-kept"):
            category, by, reason = old[addr][1], old[addr][2], old[addr][3]
        else:
            category, by, reason = ("newsletter" if list_id else "marketing" if unsub else "other"), "rule", \
                "From its headers; the AI model hasn't looked yet."
        intel.execute("""INSERT OR REPLACE INTO senders (source_id, address, name, domain, messages, unread, bytes,
                         first_date, last_date, list_id, unsubscribe, one_click, owner_wrote, inbox, category,
                         category_by, reason, input_digest, updated_at)
                         VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (source_id, addr, name or known.get(addr), addr.split("@")[-1], n, unread or 0, size or 0, first,
                       last, list_id, unsub, int(bool(one_click)), int(owner_wrote), inbox or 0, category, by, reason,
                       key, now()))
        count += 1
    present = {r[0] for r in rows}
    gone = [a for a in old if a not in present]
    intel.executemany("DELETE FROM senders WHERE source_id = ? AND address = ?", [(source_id, a) for a in gone])
    intel.commit()
    return count


def measure(intel, mail, source_id: str) -> dict:
    """Where the account's mail space goes, from the mail store; stored for the Space page."""
    def rows(sql, *args):
        return [list(r) for r in mail.execute(sql, (source_id, *args))]

    body = {
        "total": rows("SELECT count(*), coalesce(sum(size), 0) FROM messages WHERE source_id = ?")[0],
        "by_year": rows("SELECT coalesce(substr(date_utc, 1, 4), 'undated'), count(*), coalesce(sum(size), 0) "
                        "FROM messages WHERE source_id = ? GROUP BY 1 ORDER BY 1 DESC"),
        "by_mailbox": rows("SELECT mailbox, count(*), coalesce(sum(size), 0) FROM messages WHERE source_id = ? "
                           "GROUP BY 1 ORDER BY 3 DESC"),
        "largest": rows("SELECT item_id, subject, from_addr, date_utc, size, mailbox FROM messages WHERE "
                        "source_id = ? ORDER BY size DESC LIMIT 50"),
        "attachments": rows("SELECT count(*), coalesce(sum(size), 0) FROM messages WHERE source_id = ? AND "
                            "has_attachments = 1")[0],
    }
    intel.execute("INSERT OR REPLACE INTO space VALUES (?,?,?)", (source_id, json.dumps(body), now()))
    intel.commit()
    return body


def space(store_dir, source_id: str, role: str = "web") -> dict | None:
    if not store_path(store_dir, "mailintel").exists():
        return None
    with closing(open_store(store_dir, "mailintel", role)) as db:
        row = db.execute("SELECT body, updated_at FROM space WHERE source_id = ?", (source_id,)).fetchone()
    if row is None:
        return None
    return {**json.loads(row[0]), "updated_at": row[1], "by_category": totals(store_dir, source_id, role)}
