"""Searching what Towpath has read of an account's mail, including mail archived to a file and removed from the
server. Words are matched as whole words (prefixes with a trailing ``*``), all of them. A few operators narrow the
search, as in most mail apps: ``from:``, ``to:``, ``in:`` (a folder), ``before:`` and ``after:`` (a year, month or
day), ``has:attachment``, ``is:unread``, ``is:read``, ``is:flagged`` and ``larger:`` (such as ``5MB``). Nothing typed
is used as search syntax: operators become parameters, and words become quoted search terms."""

import re
from contextlib import closing

from towpath.stores import open_store, store_path

WORD = re.compile(r"[\w@.'+-]+\*?", re.UNICODE)
OPERATOR = re.compile(r'(?<!\S)(from|to|in|before|after|has|is|larger):("[^"]*"|\S+)', re.I)
DATE = re.compile(r"\d{4}(-\d{2}(-\d{2})?)?")
SIZE = re.compile(r"(\d+(?:\.\d+)?)\s*([kmg]?)b?", re.I)
IS = {"unread": "m.flags NOT LIKE '%\\Seen%'", "read": "m.flags LIKE '%\\Seen%'",
      "flagged": "m.flags LIKE '%\\Flagged%'"}


class QueryError(ValueError):
    pass


def parse(query: str) -> tuple[str, list[str], list]:
    """(the words left to search for, SQL conditions, their parameters) from the operators in ``query``."""
    where, args = [], []

    def take(m):
        key, value = m.group(1).lower(), m.group(2).strip('"').strip()
        low = value.lower()
        if key == "from":
            where.append("(lower(coalesce(m.from_addr, '')) LIKE ? OR lower(coalesce(m.from_name, '')) LIKE ?)")
            args.extend([f"%{low}%"] * 2)
        elif key == "to":
            where.append("(lower(m.to_addrs) LIKE ? OR lower(m.cc_addrs) LIKE ?)")
            args.extend([f"%{low}%"] * 2)
        elif key == "in":
            where.append("(lower(m.mailbox) = ? OR lower(m.mailbox) LIKE ?)")
            args.extend([low, f"%/{low}"])
        elif key in ("before", "after"):
            if not DATE.fullmatch(value):
                raise QueryError(f"{key}: takes a year, month or day, such as 2019, 2019-06 or 2019-06-14")
            where.append("m.date_utc < ?" if key == "before" else "m.date_utc >= ?")
            args.append(value)
        elif key == "has" and low in ("attachment", "attachments"):
            where.append("m.has_attachments = 1")
        elif key == "is" and low in IS:
            where.append(IS[low])
        elif key == "larger" and SIZE.fullmatch(value):
            number, unit = SIZE.fullmatch(value).groups()
            where.append("m.size >= ?")
            args.append(int(float(number) * {"": 1, "k": 1e3, "m": 1e6, "g": 1e9}[unit.lower()]))
        else:
            raise QueryError(f"Towpath doesn't understand {m.group(0)}")
        return " "

    return OPERATOR.sub(take, query or ""), where, args


def match_expression(query: str) -> str | None:
    terms = []
    for word in WORD.findall(query or ""):
        prefix = word.endswith("*")
        word = word.rstrip("*").replace('"', "")
        if word:
            terms.append(f'"{word}"' + ("*" if prefix else ""))
    return " ".join(terms[:12]) or None


def search(store_dir, source_id: str, query: str, limit: int = 50, before: str | None = None,
           role: str = "web") -> list[dict]:
    """Matching messages, newest first. Raises QueryError for an operator Towpath doesn't understand."""
    words, where, args = parse(query)
    expression = match_expression(words)
    if (expression is None and not where) or not store_path(store_dir, "mail").exists():
        return []
    extra = "".join(f" AND {w}" for w in where)
    with closing(open_store(store_dir, "mail", role)) as db:
        if expression is None:
            sql = ("SELECT m.item_id, m.subject, m.from_name, m.from_addr, m.date_utc, m.mailbox, a.path AS archived, "
                   "substr(coalesce(m.text, ''), 1, 160) AS snippet FROM messages m LEFT JOIN archived a ON "
                   f"a.item_id = m.item_id WHERE m.source_id = ? AND coalesce(m.date_utc, '') < ?{extra} "
                   "ORDER BY m.date_utc DESC LIMIT ?")
            params = (source_id, before or "9999", *args, limit)
        else:
            sql = ("SELECT m.item_id, m.subject, m.from_name, m.from_addr, m.date_utc, m.mailbox, a.path AS archived, "
                   "snippet(messages_text, 3, '', '', ' … ', 16) AS snippet FROM messages_text JOIN messages m ON "
                   "m.rowid = messages_text.rowid LEFT JOIN archived a ON a.item_id = m.item_id WHERE messages_text "
                   f"MATCH ? AND m.source_id = ? AND coalesce(m.date_utc, '') < ?{extra} ORDER BY m.date_utc DESC "
                   "LIMIT ?")
            params = (expression, source_id, before or "9999", *args, limit)
        rows = [dict(r) for r in db.execute(sql, params)]
    if rows:
        with closing(open_store(store_dir, "source", role)) as src:
            ids = [r["item_id"] for r in rows]
            marks = ",".join("?" for _ in ids)
            present = {r[0] for r in src.execute(
                f"SELECT item_id FROM items WHERE absent_since_run IS NULL AND item_id IN ({marks})", ids)}
        for r in rows:
            r["where"] = "server" if r["item_id"] in present else ("archive" if r["archived"] else "gone")
    return rows
