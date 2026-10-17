"""Searching what Towpath has read of an account's mail, including mail archived to a file and removed from the
server. Words are matched as whole words (prefixes with a trailing ``*``), all of them; nothing typed is used as
search syntax."""

import re
from contextlib import closing

from towpath.stores import open_store, store_path

WORD = re.compile(r"[\w@.'+-]+\*?", re.UNICODE)


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
    expression = match_expression(query)
    if expression is None or not store_path(store_dir, "mail").exists():
        return []
    with closing(open_store(store_dir, "mail", role)) as db:
        rows = [dict(r) for r in db.execute(
            "SELECT m.item_id, m.subject, m.from_name, m.from_addr, m.date_utc, m.mailbox, a.path AS archived, "
            "snippet(messages_text, 3, '', '', ' … ', 16) AS snippet FROM messages_text JOIN messages m ON "
            "m.rowid = messages_text.rowid LEFT JOIN archived a ON a.item_id = m.item_id WHERE messages_text MATCH ? "
            "AND m.source_id = ? AND coalesce(m.date_utc, '') < ? ORDER BY m.date_utc DESC LIMIT ?",
            (expression, source_id, before or "9999", limit))]
    if rows:
        with closing(open_store(store_dir, "source", role)) as src:
            ids = [r["item_id"] for r in rows]
            marks = ",".join("?" for _ in ids)
            present = {r[0] for r in src.execute(
                f"SELECT item_id FROM items WHERE absent_since_run IS NULL AND item_id IN ({marks})", ids)}
        for r in rows:
            r["where"] = "server" if r["item_id"] in present else ("archive" if r["archived"] else "gone")
    return rows
