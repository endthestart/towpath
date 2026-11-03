"""The model's part: sorting senders into categories, a few at a time."""

import time
from datetime import datetime, timezone

from towpath.mail.senders.common import (
    GROUP,
    GROUP_SCHEMA,
    INSTRUCTION,
    MODEL_BATCH,
    PROMPT,
    PROMPT_GROUP,
    ROLE,
    SCHEMA,
    SLICE_SECONDS,
    TASK,
    now,
)
from towpath.mail.senders.rebuilding import measure, rebuild
from towpath.stores import open_store, store_path


def _facts(mail, source_id: str, sender: dict, subjects_n: int = 6, snippet_chars: int = 400) -> tuple[str, list]:
    rows = mail.execute("SELECT item_id, subject, text FROM messages WHERE source_id = ? AND from_addr = ? "
                        "ORDER BY date_utc DESC LIMIT ?", (source_id, sender["address"], subjects_n)).fetchall()
    subjects = "\n".join(f"- {r[1] or '(no subject)'}" for r in rows)
    snippet = " ".join(((rows[0][2] or "") if rows else "").split())[:snippet_chars]
    return (f"Name: {sender['name'] or '(none)'}\nAddress: {sender['address']}\nMessages: {sender['messages']}\n"
            f"Mailing-list headers: {'yes' if sender['list_id'] or sender['unsubscribe'] else 'no'}\n"
            f"The owner has written to them: {'yes' if sender['owner_wrote'] else 'no'}\n"
            f"Recent subjects:\n{subjects}\nStart of the latest message: {snippet}"), [r[0] for r in rows]


def _prompt(mail, source_id: str, sender: dict) -> tuple[str, list[str]]:
    facts, items = _facts(mail, source_id, sender)
    return f"{INSTRUCTION}\n\n<sender>\n{facts}\n</sender>", items


def _group_prompt(mail, source_id: str, group: list[dict]) -> tuple[str, list[str]]:
    blocks, items = [], []
    for n, s in enumerate(group, start=1):
        facts, ids = _facts(mail, source_id, s, subjects_n=4, snippet_chars=200)
        blocks.append(f'<sender n="{n}">\n{facts}\n</sender>')
        items += ids
    head = INSTRUCTION.replace("Give a short reason", "Answer for every sender, by its number n. Give each a short "
                                                       "reason")
    return f"{head}\n\n" + "\n\n".join(blocks), items


def _settle(intel, source_id: str, s: dict, category: str, reason: str, model: str) -> None:
    if category == "cold_email" and (s["owner_wrote"] or s["messages"] > 3):
        category = "other"  # not a first contact
    intel.execute("UPDATE senders SET category = ?, category_by = 'model', reason = ?, updated_at = ? "
                  "WHERE source_id = ? AND address = ?",
                  (category, f"{reason.strip()[:160]} ({model})", now(), source_id, s["address"]))


WAITING = "FROM senders WHERE source_id = ? AND category_by = 'rule' AND owner_wrote = 0 AND category != 'notification'"


def waiting(intel, source_id: str) -> int:
    return intel.execute(f"SELECT count(*) {WAITING}", (source_id,)).fetchone()[0]


def _categorize(config, intel, mail, source_id: str, deadline: float, clock) -> tuple[int, str | None]:
    from towpath.mail.threads import _failure, item_refused
    from towpath.models import gateway

    model = config.endpoints[config.tasks[TASK]].model
    done = 0
    while clock() < deadline:
        rows = [dict(r) for r in intel.execute(
            f"SELECT * {WAITING} ORDER BY (messages >= 3) DESC, last_date DESC LIMIT ?", (source_id, MODEL_BATCH))]
        if not rows:
            return done, None
        from towpath.mail.privacy import targets

        missed = rows
        if len(rows) > 1:  # several senders in one call; any the model leaves out are asked about one by one
            group = rows[:GROUP]
            prompt, items = _group_prompt(mail, source_id, group)
            result = gateway.infer(config, TASK, prompt, "content", GROUP_SCHEMA,
                                   targets(source_id, items, [s["address"] for s in group]),
                                   prompt_version=PROMPT_GROUP, background=True)
            if result["status"] in ("refused", "disabled") and not item_refused(result):
                return done, "refused"
            if _failure(result):
                return done, _failure(result)
            answered = {}  # refused for one kept sender: each is asked about alone below
            if result["status"] == "ok":
                answered = {a["n"]: a for a in result["output"]["senders"] if 1 <= a["n"] <= len(group)}
                for n, a in answered.items():
                    _settle(intel, source_id, group[n - 1], a["category"], a["reason"], model)
                intel.commit()
                done += len(answered)
            missed = [s for n, s in enumerate(group, start=1) if n not in answered]
        for s in missed:
            if clock() >= deadline:
                return done, None
            prompt, items = _prompt(mail, source_id, s)
            result = gateway.infer(config, TASK, prompt, "content", SCHEMA, targets(source_id, items, [s["address"]]),
                                   prompt_version=PROMPT, background=True)
            if result["status"] == "ok":
                _settle(intel, source_id, s, result["output"]["category"], result["output"]["reason"], model)
            elif item_refused(result):  # kept from this model: the rule's category stands
                intel.execute("UPDATE senders SET category_by = 'rule-kept' WHERE source_id = ? AND address = ?",
                              (source_id, s["address"]))
            elif result["status"] in ("refused", "disabled"):
                return done, "refused"
            elif _failure(result):
                return done, _failure(result)
            else:
                intel.execute("UPDATE senders SET category_by = 'rule-kept' WHERE source_id = ? AND address = ?",
                              (source_id, s["address"]))
            intel.commit()
            done += 1
    return done, None


def categorize_pending(config, slice_seconds: float = SLICE_SECONDS, clock=time.monotonic) -> list[dict]:
    """One bounded slice per managed account: refresh sender totals when mail changed, then the model's
    categories."""
    from towpath.mail import analysis, threads

    summaries = []
    for sid in analysis.eligible(config):
        if not store_path(config.store_dir, "mail").exists():
            continue
        deadline = clock() + slice_seconds
        intel = open_store(config.store_dir, "mailintel", ROLE)
        mail = open_store(config.store_dir, "mail", ROLE)
        try:
            state = threads._progress(intel, sid)
            top = mail.execute("SELECT max(rowid) FROM messages WHERE source_id = ?", (sid,)).fetchone()[0] or 0
            rebuilt = 0
            if state.get("senders_rowid") != top:
                rebuilt = rebuild(intel, mail, sid, threads.owner(config.store_dir, sid, mail), config.store_dir)
                measure(intel, mail, sid)
                from towpath.mail import notify

                notify.measure(config.store_dir, sid)
                state = {**threads._progress(intel, sid), "senders_rowid": top}
                threads._set_progress(intel, sid, state)
            done, stop = 0, None
            if not (state.get("senders_retry_at") and now() < state["senders_retry_at"]) and \
                    threads.blocked(config, sid, TASK) is None:
                if threads.ready(config, TASK):
                    done, stop = _categorize(config, intel, mail, sid, deadline, clock)
                else:
                    stop = "transient"
            if stop in ("busy", "transient"):
                from datetime import timedelta

                wait = threads.BUSY_SECONDS if stop == "busy" else threads.RETRY_SECONDS
                threads._set_progress(intel, sid, {**threads._progress(intel, sid), "senders_retry_at": (
                    datetime.now(timezone.utc) + timedelta(seconds=wait)).isoformat(timespec="seconds")})
            if rebuilt or done:
                summaries.append({"source_id": sid, "step": "mail-senders", "senders": rebuilt, "categorized": done})
        finally:
            intel.close()
            mail.close()
    return summaries
