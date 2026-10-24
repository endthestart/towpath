"""Mail searches in plain English: the owner's question turned into mail search words and operators by the model.

The model sees the question, the operators and the account's folder names, never any mail. Its answer is checked by
the mail search's own parser; one that doesn't parse falls back to the question's words. The owner sees the search
that ran and can edit it.
"""

import re
import sqlite3
from contextlib import closing
from datetime import date, datetime, timezone

from towpath.stores import open_store, store_path

TASK = "search.translate"
PROMPT = "mail.search-translate/1"
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["query"],
          "properties": {"query": {"type": "string", "maxLength": 200}}}
SYNTAX = """Turn the owner's question into one mail search. Plain words must all appear in the subject, sender or
text (end a word with * to match its start). Narrow it with:
- from:name-or-address, to:name-or-address
- in:folder (one of the folders listed below)
- after:YYYY, after:YYYY-MM or after:YYYY-MM-DD (on or after); before: the same (strictly before)
- has:attachment, is:unread, is:read, is:flagged
- larger:5MB (or KB, GB)
Prefer operators to words where they fit, and keep words few. Today is {today}."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def fallback(question: str) -> str:
    words = [w for w in re.findall(r"[\w'-]+", question) if len(w) > 2]
    return " ".join(words[:6])


def ask(store_dir, source_id: str, text: str) -> int:
    text = " ".join(text.split())[:300]
    if not text:
        raise ValueError("ask something")
    with closing(open_store(store_dir, "queue", "web")) as q:
        seq = q.execute("INSERT INTO mail_search_questions (source_id, text, author, created_at) VALUES "
                        "(?, ?, 'owner', ?)", (source_id, text, now())).lastrowid
        q.commit()
    return seq


def question(store_dir, seq: int) -> dict | None:
    with closing(open_store(store_dir, "queue", "web")) as q:
        row = q.execute("SELECT source_id, text FROM mail_search_questions WHERE seq = ?", (seq,)).fetchone()
    return {"source_id": row[0], "text": row[1]} if row else None


def answer(store_dir, seq: int, role: str = "web") -> dict | None:
    if not store_path(store_dir, "derived").exists():
        return None
    with closing(open_store(store_dir, "derived", role)) as d:
        try:
            row = d.execute("SELECT query, status FROM mail_search_answers WHERE seq = ?", (seq,)).fetchone()
        except sqlite3.OperationalError:
            return None
    return {"query": row[0], "status": row[1]} if row else None


def translate_pending(config) -> list[dict]:
    from towpath import decisions
    from towpath.mail import search as mail_search
    from towpath.mail.threads import ready
    from towpath.models import gateway

    try:
        with closing(open_store(config.store_dir, "queue", "worker")) as q:
            asked = [dict(r) for r in q.execute("SELECT * FROM mail_search_questions ORDER BY seq")]
    except sqlite3.OperationalError:
        return []
    if not asked:
        return []
    with closing(open_store(config.store_dir, "derived", "worker")) as d:
        done = {r[0] for r in d.execute("SELECT seq FROM mail_search_answers")}
    todo = [a for a in asked if a["seq"] not in done]
    if not todo:
        return []
    endpoint = config.tasks.get(TASK)
    usable = bool(endpoint) and decisions.model_granted(config, endpoint, "content", role="worker") and \
        ready(config, TASK)
    out = 0
    with closing(open_store(config.store_dir, "derived", "worker")) as d:
        for a in todo:
            query, status = fallback(a["text"]), "words"
            if usable:
                boxes = []
                if store_path(config.store_dir, "mail").exists():
                    with closing(open_store(config.store_dir, "mail", "worker")) as mail:
                        boxes = [r[0] for r in mail.execute("SELECT DISTINCT mailbox FROM messages WHERE source_id = ? "
                                                            "LIMIT 40", (a["source_id"],))]
                prompt = (SYNTAX.format(today=date.today().isoformat()) + f"\n\nFolders: {', '.join(boxes) or '-'}"
                          f"\n\nQuestion: {a['text']}")
                result = gateway.infer(config, TASK, prompt, "content", SCHEMA, prompt_version=PROMPT,
                                       background=False)
                if result["status"] == "ok":
                    candidate = " ".join(result["output"]["query"].split())
                    try:
                        mail_search.parse(candidate)
                        query, status = candidate, "translated"
                    except mail_search.QueryError:
                        status = "words-after-invalid"
            d.execute("INSERT OR REPLACE INTO mail_search_answers VALUES (?,?,?,?)", (a["seq"], query, status, now()))
            d.commit()
            out += 1
    return [{"step": "mail-search-questions", "translated": out}]
