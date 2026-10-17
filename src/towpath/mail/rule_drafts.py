"""Rules described in plain English: the model drafts a rule; the owner reviews it in the rule form before saving.

The model sees the description, the categories and the kinds of change a rule can make, never any mail. Its draft is
checked by the same validation as a rule the owner types; a draft that doesn't validate becomes a rule with the
description as its condition in words, for the owner to finish.
"""

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone

from towpath.stores import open_store, store_path

TASK = "mail.classify"
PROMPT = "mail.rule-draft/1"


def _schema():
    from towpath.mail import actions, senders

    kinds = [k for k in actions.TYPES if k not in actions.APPEND_TYPES and k not in actions.SENDER_TYPES]

    return {"type": "object", "additionalProperties": False,
            "required": ["name", "from", "subject", "categories", "instructions", "actions", "scope"],
            "properties": {
                "name": {"type": "string", "maxLength": 80},
                "from": {"type": "string", "maxLength": 120}, "subject": {"type": "string", "maxLength": 120},
                "categories": {"type": "array", "items": {"type": "string", "enum": list(senders.CATEGORIES)}},
                "instructions": {"type": "string", "maxLength": 300},
                "actions": {"type": "array", "minItems": 1, "maxItems": 3, "items": {
                    "type": "object", "additionalProperties": False, "required": ["type", "value"],
                    "properties": {"type": {"type": "string", "enum": kinds},
                                   "value": {"type": "string", "maxLength": 60}}}},
                "scope": {"type": "string", "enum": ["new", "all"]}}}


INSTRUCTION = """Turn the owner's description into one mail rule. Fields:
- name: a short name for the rule.
- from, subject: text that must appear in the sender or the subject ("" if not needed); use *@domain for a domain.
- categories: sender categories it applies to ([] if any): {categories}.
- instructions: a condition in plain words for what static fields can't express ("" if not needed).
- actions: what to do; value is the label for add_keyword, or the folder for move, else "".
- scope: "new" for new mail only, or "all" if the owner clearly means existing mail too.
Prefer categories and from/subject over instructions where they fit."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ask(store_dir, source_id: str, text: str) -> int:
    text = " ".join(text.split())[:400]
    if not text:
        raise ValueError("describe the rule")
    with closing(open_store(store_dir, "queue", "web")) as q:
        seq = q.execute("INSERT INTO rule_requests (source_id, text, author, created_at) VALUES (?, ?, 'owner', ?)",
                        (source_id, text, now())).lastrowid
        q.commit()
    return seq


def request(store_dir, seq: int) -> dict | None:
    with closing(open_store(store_dir, "queue", "web")) as q:
        row = q.execute("SELECT * FROM rule_requests WHERE seq = ?", (seq,)).fetchone()
    return dict(row) if row else None


def draft(store_dir, seq: int, role: str = "web") -> dict | None:
    if not store_path(store_dir, "derived").exists():
        return None
    with closing(open_store(store_dir, "derived", role)) as d:
        try:
            row = d.execute("SELECT body, status FROM rule_drafts WHERE seq = ?", (seq,)).fetchone()
        except sqlite3.OperationalError:
            return None
    return {"body": json.loads(row[0]) if row[0] else None, "status": row[1]} if row else None


def to_body(out: dict) -> dict:
    acts = []
    for a in out.get("actions", []):
        value = (a.get("value") or "").strip()
        params = {"keyword": value} if a["type"] in ("add_keyword", "remove_keyword") else (
            {"mailbox": value} if a["type"] == "move" else {})
        acts.append({"type": a["type"], "params": params})
    return {"name": out.get("name", ""), "instructions": out.get("instructions", ""),
            "conditions": {"from": out.get("from", ""), "subject": out.get("subject", ""),
                           "categories": out.get("categories", []), "mailbox": "INBOX"},
            "actions": acts, "scope": out.get("scope", "new")}


def draft_pending(config) -> list[dict]:
    from towpath import decisions
    from towpath.mail import rules, senders
    from towpath.mail.threads import ready
    from towpath.models import gateway

    try:
        with closing(open_store(config.store_dir, "queue", "worker")) as q:
            asked = [dict(r) for r in q.execute("SELECT * FROM rule_requests ORDER BY seq")]
    except sqlite3.OperationalError:
        return []
    if not asked:
        return []
    with closing(open_store(config.store_dir, "derived", "worker")) as d:
        done = {r[0] for r in d.execute("SELECT seq FROM rule_drafts")}
    todo = [a for a in asked if a["seq"] not in done]
    endpoint = config.tasks.get(TASK)
    usable = bool(todo) and bool(endpoint) and decisions.model_granted(config, endpoint, "content", role="worker") \
        and ready(config, TASK)
    out = 0
    with closing(open_store(config.store_dir, "derived", "worker")) as d:
        for a in todo:
            body, status = {"name": a["text"][:60], "instructions": a["text"], "conditions": {"mailbox": "INBOX"},
                            "actions": [], "scope": "new"}, "words"
            if usable:
                prompt = INSTRUCTION.format(categories=", ".join(senders.CATEGORIES)) + f"\n\nDescription: {a['text']}"
                result = gateway.infer(config, TASK, prompt, "content", _schema(), prompt_version=PROMPT)
                if result["status"] == "ok":
                    candidate = to_body(result["output"])
                    try:
                        rules.validate(candidate)
                        body, status = candidate, "drafted"
                    except (rules.RuleError, ValueError):
                        status = "words-after-invalid"
            d.execute("INSERT OR REPLACE INTO rule_drafts VALUES (?,?,?,?)", (a["seq"], json.dumps(body), status,
                                                                             now()))
            d.commit()
            out += 1
    return [{"step": "rule-drafts", "drafted": out}] if out else []
