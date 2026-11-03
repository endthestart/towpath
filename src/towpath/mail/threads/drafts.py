"""Reply drafts in the owner's voice, and the owner's notes for them."""

import json
from datetime import datetime, timedelta, timezone

from towpath.mail.threads.building import marks, _messages
from towpath.mail.threads.classify import conversation_text, _failure, item_refused
from towpath.mail.threads.common import (
    DRAFT_DAYS,
    DRAFT_INSTRUCTION,
    DRAFT_SCHEMA,
    PROMPT_DRAFT,
    ROLE,
    TASK_DRAFT,
    UNTRUSTED,
    now,
)

FOLLOW_UP_INSTRUCTION = f"""You draft a short follow-up for the owner of a mailbox. {UNTRUSTED}

The owner wrote last and nobody has answered. Write a brief, friendly nudge in the owner's own voice (examples of how
they write are given) that refers to what they asked or offered, without repeating it in full and without
complaining about the wait. Never invent facts, dates or commitments; use a placeholder in square brackets where the
owner must add something. No subject line and no signature block."""
NOTES_MAX = 2000


def notes(store_dir, source_id: str, role: str = ROLE) -> str:
    """What the owner wrote for drafts to use: availability, phone number, how they sign off, their style."""
    from towpath.mail import settings

    return settings._get(store_dir, source_id, "draft_notes", role) or ""


def set_notes(store_dir, source_id: str, text: str, author: str = "owner") -> None:
    from towpath.mail import settings

    text = (text or "").strip()
    if len(text) > NOTES_MAX:
        raise ValueError(f"notes for drafts are at most {NOTES_MAX} characters")
    settings._set(store_dir, source_id, "draft_notes", text, "mail-draft-notes", author)


def _style_examples(mail, source_id: str, me: dict, to: str | None, limit: int = 3) -> list[str]:
    boxes = sorted(me["sent"])
    marks_ = ",".join("?" for _ in boxes)
    rows = mail.execute(f"SELECT text FROM messages WHERE source_id = ? AND mailbox IN ({marks_}) AND text IS NOT "
                        f"NULL AND length(text) BETWEEN 40 AND 1200 AND to_addrs LIKE ? ORDER BY date_utc DESC "
                        f"LIMIT ?", (source_id, *boxes, f'%{to or "@"}%', limit)).fetchall()
    if len(rows) < limit:
        rows += mail.execute(f"SELECT text FROM messages WHERE source_id = ? AND mailbox IN ({marks_}) AND text IS NOT "
                             f"NULL AND length(text) BETWEEN 40 AND 800 ORDER BY date_utc DESC LIMIT ?",
                             (source_id, *boxes, limit - len(rows))).fetchall()
    return [r[0] for r in rows]


def _to_draft(store_dir, intel, source_id: str) -> list[tuple]:
    """Conversations waiting on the owner with recent mail, then follow-ups due: (key, kind, current, drafted)."""
    from towpath.mail import notify

    owner_marks = marks(store_dir, source_id)
    since = (datetime.now(timezone.utc) - timedelta(days=DRAFT_DAYS)).isoformat()
    rows = [(key, "reply", current, drafted) for key, state, current, drafted in intel.execute(
        "SELECT t.thread_key, t.state, t.input_digest, d.input_digest FROM threads t LEFT JOIN drafts d USING "
        "(source_id, thread_key) WHERE t.source_id = ? AND t.kind = 'conversation' AND t.last_from_owner = 0 AND "
        "t.last_date >= ? ORDER BY t.last_date DESC", (source_id, since)).fetchall()
        if owner_marks.get(key, state) == "needs_owner_reply"]
    days = notify.follow_up_days(store_dir, source_id)
    if days:
        for key, _ in notify.follow_up_due(store_dir, source_id, intel, days):
            current, drafted = intel.execute(
                "SELECT t.input_digest, d.input_digest FROM threads t LEFT JOIN drafts d USING (source_id, "
                "thread_key) WHERE t.source_id = ? AND t.thread_key = ?", (source_id, key)).fetchone()
            rows.append((key, "follow-up", current, drafted))
    return rows


def drafts_waiting(store_dir, intel, source_id: str) -> int:
    return sum(drafted != current for _, _, current, drafted in _to_draft(store_dir, intel, source_id))


def _draft(config, intel, mail, source_id: str, me: dict, deadline: float, clock) -> tuple[int, str | None]:
    from towpath.models import gateway

    endpoint = config.endpoints[config.tasks[TASK_DRAFT]]
    rows = _to_draft(config.store_dir, intel, source_id)
    owner_notes = notes(config.store_dir, source_id)
    done = 0
    for key, kind, current, drafted in rows:
        if drafted == current:
            continue
        if clock() >= deadline:
            break
        msgs = _messages(mail, source_id, key)
        if not msgs:
            continue
        last = msgs[-1]
        other = next((m for m in reversed(msgs) if m["from_addr"] not in me["addresses"]), None)
        to = other["from_addr"] if kind == "reply" and other else (json.loads(last["to_addrs"] or "[]") or [None])[0]
        examples = "\n\n".join(f"Example {i + 1}:\n{e}" for i, e in enumerate(
            _style_examples(mail, source_id, me, to)))
        told = f"Notes from the owner, which you may use:\n{owner_notes}\n\n" if owner_notes else ""
        prompt = (f"{DRAFT_INSTRUCTION if kind == 'reply' else FOLLOW_UP_INSTRUCTION}\n\n"
                  f"The owner's name: {me['name'] or 'unknown'}\n\n{told}"
                  f"How the owner writes:\n{examples or '(no examples)'}\n\n"
                  f"<conversation>\n{conversation_text(msgs, me)}\n</conversation>")
        from towpath.mail.privacy import targets

        result = gateway.infer(config, TASK_DRAFT, prompt, "content", DRAFT_SCHEMA,
                               targets(source_id, [m["item_id"] for m in msgs][-20:], [m["from_addr"] for m in msgs]),
                               prompt_version=PROMPT_DRAFT, background=True)
        if result["status"] == "ok" and result["output"]["body"].strip():
            intel.execute("INSERT OR REPLACE INTO drafts VALUES (?,?,?,?,?,?,?)",
                          (source_id, key, last["item_id"], current, result["output"]["body"].strip(),
                           f"{endpoint.model}/{PROMPT_DRAFT}", now()))
            intel.commit()
            done += 1
        elif item_refused(result) or result["status"] not in ("refused", "disabled") and not _failure(result):
            # kept from this model, or an empty or unusable answer: no draft for this version of the conversation,
            # and the next ones go on (a new message asks again)
            intel.execute("INSERT OR REPLACE INTO drafts VALUES (?,?,?,?,?,?,?)",
                          (source_id, key, last["item_id"], current, "",
                           "refused" if item_refused(result) else "no-answer", now()))
            intel.commit()
        elif result["status"] in ("refused", "disabled"):
            return done, "refused"
        elif _failure(result):
            return done, _failure(result)
    return done, None
