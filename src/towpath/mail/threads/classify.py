"""The model's part in tracking: reading a conversation and deciding its state."""

from datetime import datetime, timedelta, timezone

from towpath.mail.threads.building import marks, _messages, _save, summarize
from towpath.mail.threads.common import (
    MODEL_BATCH,
    NOT_PEOPLE,
    OLD_DAYS,
    PROMPT_STATE,
    ROLE,
    STATE_INSTRUCTION,
    STATE_SCHEMA,
    TASK_STATE,
)


def conversation_text(msgs: list[dict], me: dict, latest_chars: int = 6000, each_chars: int = 1500) -> str:
    """First message, the last eight, the latest in full (up to a limit), oldest first."""
    shown = msgs if len(msgs) <= 9 else [msgs[0]] + msgs[-8:]
    parts = []
    if len(msgs) > 9:
        parts.append(f"[{len(msgs) - 9} earlier messages left out]")
    for i, m in enumerate(shown):
        who = "OWNER" if (m["from_addr"] in me["addresses"] or m["mailbox"] in me["sent"]) else (
            f"{m['from_name'] or ''} <{m['from_addr'] or 'unknown'}>".strip())
        limit = latest_chars if i == len(shown) - 1 else each_chars
        body = (m["text"] or "").strip()
        body = body[:limit] + (" […]" if len(body) > limit else "")
        parts.append(f"--- {m['date_utc'] or 'undated'} from {who}\nSubject: {m['subject'] or ''}\n{body}")
    return "\n\n".join(parts)


def _failure(result: dict) -> str | None:
    """'busy', 'transient' or 'paced' when the model couldn't take the call now (try again later), else None."""
    return result["failure"] if result["status"] == "unavailable" else None


def item_refused(result: dict) -> bool:
    """The gateway refused because of the items named (kept from cloud models, or excluded), not for every call."""
    return result.get("status") == "refused" and str(result.get("reason", "")).startswith("item ")


def blocked(config, source_id: str, task: str, role: str = ROLE) -> str | None:
    """Why the model can't be used for this account and task now, or None."""
    from towpath import decisions

    endpoint_id = config.tasks.get(task)
    if endpoint_id is None:
        return "No AI model is connected."
    if not decisions.model_granted(config, endpoint_id, "content", role=role):
        return "The AI model chosen for this isn't allowed to read email text yet."
    return None


def ready(config, task: str) -> bool:
    """Whether the task's model has answered Towpath's made-up probe questions (probing it now if needed)."""
    from towpath.models import gateway

    endpoint_id = config.tasks[task]
    report = gateway.capability_report(config, endpoint_id)
    if report is None or not report["capabilities"].get("chat"):
        gateway.probe(config, endpoint_id)  # synthetic prompts only
        report = gateway.capability_report(config, endpoint_id)
    return bool(report and report["capabilities"].get("chat"))


def _owner_examples(intel, store_dir, source_id: str, limit: int = 6) -> str:
    """The owner's own marks, as examples of how they judge conversations (subjects and states only)."""
    owner_marks = marks(store_dir, source_id)
    if not owner_marks:
        return ""
    keys = list(owner_marks)[-200:]
    rows = intel.execute(f"SELECT thread_key, subject FROM threads WHERE source_id = ? AND thread_key IN "
                         f"({','.join('?' for _ in keys)})", (source_id, *keys)).fetchall()
    lines = [f'- "{subject or "(no subject)"}": {owner_marks[key]}' for key, subject in rows[-limit:]]
    return ("\n\nHow the owner has judged other conversations (follow their judgement):\n" + "\n".join(lines)
            if lines else "")


def _settle_by_sender(config, intel, mail, source_id: str, me: dict) -> int:
    """Conversations whose latest message is from a company or service: FYI by rule, not for the model. A sender the
    owner later calls a person sends theirs back to the model."""
    from towpath.mail import senders

    cats = senders.categories(config.store_dir, source_id, ROLE)
    since = (datetime.now(timezone.utc) - timedelta(days=OLD_DAYS)).isoformat()
    changed = 0
    for key, last_from in intel.execute(
            "SELECT thread_key, last_from FROM threads WHERE source_id = ? AND state_by = 'rule' AND kind = "
            "'conversation' AND last_from_owner = 0 AND last_date >= ?", (source_id, since)).fetchall():
        category = cats.get(last_from or "")
        if category in NOT_PEOPLE:
            intel.execute("UPDATE threads SET state = 'fyi', state_by = 'rule-sender', reason = ? WHERE source_id = ? "
                          "AND thread_key = ?", (f"From a sender sorted as {senders.CATEGORIES[category].lower()}.",
                                                 source_id, key))
            changed += 1
    for key, last_from in intel.execute("SELECT thread_key, last_from FROM threads WHERE source_id = ? AND state_by = "
                                        "'rule-sender'", (source_id,)).fetchall():
        if cats.get(last_from or "") not in NOT_PEOPLE:  # the rules' own state again, then the model's turn
            msgs = _messages(mail, source_id, key)
            if msgs:
                _save(intel, summarize(source_id, key, msgs, me), "rule")
            else:
                intel.execute("UPDATE threads SET state_by = 'rule' WHERE source_id = ? AND thread_key = ?",
                              (source_id, key))
            changed += 1
    intel.commit()
    return changed


WAITING = "FROM threads WHERE source_id = ? AND state_by = 'rule' AND kind = 'conversation' AND last_date >= ?"


def _since(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def conversations_waiting(intel, source_id: str, days: int = OLD_DAYS) -> int:
    return intel.execute(f"SELECT count(*) {WAITING}", (source_id, _since(days))).fetchone()[0]


def _classify(config, intel, mail, source_id: str, me: dict, deadline: float, clock,
              days: int = OLD_DAYS) -> tuple[int, str | None]:
    from towpath.models import gateway

    done = 0
    endpoint = config.endpoints[config.tasks[TASK_STATE]]
    examples = _owner_examples(intel, config.store_dir, source_id)
    while clock() < deadline:
        rows = intel.execute(f"SELECT thread_key {WAITING} ORDER BY last_date DESC LIMIT ?",
                             (source_id, _since(days), MODEL_BATCH)).fetchall()
        if not rows:
            return done, None
        for (key,) in rows:
            if clock() >= deadline:
                return done, None
            msgs = _messages(mail, source_id, key)
            if not msgs:  # its messages were read again under another thread
                intel.execute("UPDATE threads SET state_by = 'rule-kept' WHERE source_id = ? AND thread_key = ?",
                              (source_id, key))
                intel.commit()
                continue
            t = summarize(source_id, key, msgs, me)
            prompt = (f"{STATE_INSTRUCTION}{examples}\n\n<conversation>\n{conversation_text(msgs, me)}\n"
                      f"</conversation>")
            from towpath.mail.privacy import targets

            result = gateway.infer(config, TASK_STATE, prompt, "content", STATE_SCHEMA,
                                   targets(source_id, [m["item_id"] for m in msgs][-20:],
                                           [m["from_addr"] for m in msgs]), prompt_version=PROMPT_STATE,
                                   background=True)
            if result["status"] == "ok":
                t["state"], t["reason"] = result["output"]["state"], result["output"]["reason"].strip()[:200]
                _save(intel, t, "model", f"{endpoint.model}/{PROMPT_STATE}")
            elif item_refused(result):  # kept from this model: the rules' state stands, and the rest go on
                _save(intel, t, "rule-kept")
            elif result["status"] in ("refused", "disabled"):
                return done, "refused"
            elif _failure(result):
                return done, _failure(result)
            else:  # the model's answers didn't fit: keep the rule's state and don't ask again
                _save(intel, t, "rule-kept")
            intel.commit()
            done += 1
    return done, None
