"""Plain-English searches: the owner's question turned into Towpath's own query syntax by a local model.

The model sees the question, the syntax, and the names of the indexed roots, their top folders and the mailboxes,
never any results. Its answer is parsed by the search contract like a typed query; anything that doesn't parse
falls back to the question's own words. The owner sees the query that runs and can edit it.
"""

import re
import sqlite3
from contextlib import closing
from datetime import date, datetime, timezone

from towpath.stores import open_store, store_path

TASK = "search.translate"
PROMPT = "search.translate/1"
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["query"],
          "properties": {"query": {"type": "string", "maxLength": 300}}}
SYNTAX = """Towpath's search syntax: plain words match names, subjects, senders and text; add filters as key:value.
- extension:pdf (file type by extension), type:image/ (media type prefix), kind:file or kind:mail-message or
  kind:mail-part (attachments)
- after:YYYY-MM-DD, before:YYYY-MM-DD (dates), modified:2019 or modified:2019-05 (files changed then)
- in:"root/folder" (inside a folder; roots and folders are listed below) or in:INBOX (a mailbox)
- size:>10MB, size:<1KB, size:10MB..2GB
Answer with one query using only this syntax. Prefer filters to words where they fit. Today is {today}."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _places(config) -> str:
    lines = []
    if store_path(config.store_dir, "files").exists():
        with closing(open_store(config.store_dir, "files", "worker")) as db:
            try:
                rows = db.execute("SELECT root_alias, path FROM space_folders WHERE parent IS NULL OR parent = '' "
                                  "OR instr(path, '/') = 0 ORDER BY bytes DESC LIMIT 60").fetchall()
            except sqlite3.OperationalError:
                rows = []
        roots: dict[str, list] = {}
        for root, path in rows:
            roots.setdefault(root, []).append(path)
        lines += [f"Root {r}: folders {', '.join(p for p in paths if p)[:600]}" for r, paths in roots.items()]
    if store_path(config.store_dir, "mail").exists():
        with closing(open_store(config.store_dir, "mail", "worker")) as db:
            boxes = [r[0] for r in db.execute("SELECT DISTINCT mailbox FROM messages LIMIT 40")]
        if boxes:
            lines.append("Mailboxes: " + ", ".join(boxes))
    return "\n".join(lines) or "(no folders indexed yet)"


def fallback(question: str) -> str:
    """The question's own words, as a plain search."""
    words = [w for w in re.findall(r"[\w'-]+", question) if len(w) > 2]
    return " ".join(words[:8])


def translate_pending(config) -> list[dict]:
    from towpath import decisions
    from towpath.mail.threads import ready
    from towpath.models import gateway
    from towpath.unified.contracts import ContractError, Filters

    if not store_path(config.store_dir, "queue").exists():
        return []
    try:
        with closing(open_store(config.store_dir, "queue", "worker")) as q:
            asked = [dict(r) for r in q.execute("SELECT * FROM search_questions ORDER BY seq")]
    except sqlite3.OperationalError:
        return []
    with closing(open_store(config.store_dir, "derived", "worker")) as d:
        done = {r[0] for r in d.execute("SELECT seq FROM search_answers")}
    todo = [a for a in asked if a["seq"] not in done]
    if not todo:
        return []
    endpoint = config.tasks.get(TASK)
    usable = bool(endpoint) and decisions.model_granted(config, endpoint, "content", role="worker") and ready(config,
                                                                                                              TASK)
    places = _places(config) if usable else ""
    out = 0
    with closing(open_store(config.store_dir, "derived", "worker")) as d:
        for a in todo:
            query, status = fallback(a["text"]), "words"
            if usable:
                prompt = (SYNTAX.format(today=date.today().isoformat()) + f"\n\n{places}\n\nQuestion: {a['text']}")
                result = gateway.infer(config, TASK, prompt, "content", SCHEMA, prompt_version=PROMPT,
                                       background=False)
                if result["status"] == "ok":
                    try:
                        Filters.parse(result["output"]["query"])
                        query, status = result["output"]["query"].strip(), "translated"
                    except ContractError:
                        status = "words-after-invalid"
            d.execute("INSERT OR REPLACE INTO search_answers VALUES (?,?,?,?)", (a["seq"], query, status, now()))
            d.commit()
            out += 1
    return [{"step": "search-questions", "translated": out}]


def answer(store_dir, seq: int, role: str = "web") -> dict | None:
    if not store_path(store_dir, "derived").exists():
        return None
    with closing(open_store(store_dir, "derived", role)) as d:
        try:
            row = d.execute("SELECT query, status FROM search_answers WHERE seq = ?", (seq,)).fetchone()
        except sqlite3.OperationalError:
            return None
    return {"query": row[0], "status": row[1]} if row else None


def ask(store_dir, text: str) -> int:
    text = " ".join(text.split())[:300]
    if not text:
        raise ValueError("ask something")
    with closing(open_store(store_dir, "queue", "web")) as q:
        seq = q.execute("INSERT INTO search_questions (text, author, created_at) VALUES (?, 'owner', ?)",
                        (text, now())).lastrowid
        q.commit()
    return seq


def question(store_dir, seq: int) -> str | None:
    with closing(open_store(store_dir, "queue", "web")) as q:
        row = q.execute("SELECT text FROM search_questions WHERE seq = ?", (seq,)).fetchone()
    return row[0] if row else None

