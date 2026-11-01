"""Alerts on important mail, follow-up reminders, and labels that track who owes a reply."""

from datetime import datetime, timedelta

from towpath.mail import actions
from towpath.mail.notify.common import (
    FOLLOW_UP_LABEL,
    FOLLOW_UP_WINDOW_DAYS,
    REPLY_LABELS,
    REPLY_LABELS_WINDOW_DAYS,
    ROLE,
    alert_setting,
    follow_up_days,
    now,
    _propose,
    reply_labels_setting,
)
from towpath.stores import open_store, store_path


def alerts_pending(config) -> list[dict]:
    from towpath.mail import analysis, threads

    out = []
    for sid in analysis.eligible(config):
        setting = alert_setting(config.store_dir, sid)
        if setting["mode"] == "off" or not store_path(config.store_dir, "mailintel").exists():
            continue
        owner_marks = threads.marks(config.store_dir, sid)
        intel = open_store(config.store_dir, "mailintel", ROLE)
        mail = open_store(config.store_dir, "mail", ROLE)
        made = 0
        try:
            rows = intel.execute("SELECT t.thread_key, t.state, t.last_item FROM threads t LEFT JOIN alerts a ON "
                                 "a.source_id = t.source_id AND a.thread_key = t.thread_key AND a.last_item = "
                                 "t.last_item WHERE t.source_id = ? AND t.kind = 'conversation' AND "
                                 "t.last_from_owner = 0 AND t.last_date >= ? AND a.proposal_id IS NULL",
                                 (sid, setting["since"])).fetchall()
            for key, state, last_item in rows:
                if owner_marks.get(key, state) != "needs_owner_reply":
                    continue
                m = mail.execute("SELECT * FROM messages WHERE item_id = ?", (last_item,)).fetchone()
                if m is None:
                    continue
                kind, params = ("flag", {}) if setting["mode"] == "flag" else ("add_keyword", {"keyword": "Important"})
                body = actions.make(sid, kind, actions.message_target(dict(m)), params,
                                    reason="Someone is waiting for your reply.", producer=f"alert:{sid}")
                intel.execute("INSERT OR IGNORE INTO alerts VALUES (?,?,?,?,?)",
                              (sid, key, last_item, _propose(config, body), now().isoformat(timespec="seconds")))
                made += 1
            intel.commit()
        finally:
            intel.close()
            mail.close()
        if made:
            out.append({"source_id": sid, "step": "mail-alerts", "alerts": made})
    return out


# -- follow-up reminders ------------------------------------------------------------------------------------------

def follow_up_due(store_dir, source_id: str, intel, days: int, role: str = ROLE) -> list[tuple]:
    """(thread_key, last_item) of conversations the owner wrote last in, unanswered for ``days``."""
    from towpath.mail import threads

    owner_marks = threads.marks(store_dir, source_id, role)
    due = (now() - timedelta(days=days)).isoformat()
    since = (now() - timedelta(days=days + FOLLOW_UP_WINDOW_DAYS)).isoformat()
    rows = intel.execute("SELECT thread_key, state, last_item FROM threads WHERE source_id = ? AND kind = "
                         "'conversation' AND last_from_owner = 1 AND last_date <= ? AND last_date >= ? "
                         "ORDER BY last_date DESC", (source_id, due, since)).fetchall()
    return [(k, item) for k, state, item in rows if owner_marks.get(k, state) == "awaiting_others"]


def follow_ups_pending(config) -> list[dict]:
    from towpath.mail import analysis

    out = []
    for sid in analysis.eligible(config):
        days = follow_up_days(config.store_dir, sid)
        if not store_path(config.store_dir, "mailintel").exists():
            continue
        intel = open_store(config.store_dir, "mailintel", ROLE)
        mail = open_store(config.store_dir, "mail", ROLE)
        made = cleared = 0
        try:
            def target(item, mail=mail):
                m = mail.execute("SELECT * FROM messages WHERE item_id = ?", (item,)).fetchone()
                return actions.message_target(dict(m)) if m else None

            if days:
                done = {(r[0], r[1]) for r in intel.execute(
                    "SELECT thread_key, last_item FROM follow_ups WHERE source_id = ?", (sid,))}
                for key, item in follow_up_due(config.store_dir, sid, intel, days):
                    found = target(item) if (key, item) not in done else None
                    if found is None:
                        continue
                    body = actions.make(sid, "add_keyword", found, {"keyword": FOLLOW_UP_LABEL},
                                        reason=f"Nobody has answered for {days} days.", producer=f"follow-up:{sid}")
                    intel.execute("INSERT OR IGNORE INTO follow_ups VALUES (?,?,?,?,?,NULL)",
                                  (sid, key, item, _propose(config, body), now().isoformat(timespec="seconds")))
                    made += 1
            # Someone answered, or the conversation is no longer awaiting a reply (the owner's mark, or the model):
            # take the label off again.
            from towpath.mail import threads

            owner_marks = threads.marks(config.store_dir, sid)
            for key, item, state, from_owner in intel.execute(
                    "SELECT f.thread_key, f.last_item, t.state, t.last_from_owner FROM follow_ups f JOIN threads t "
                    "USING (source_id, thread_key) WHERE f.source_id = ? AND f.cleared_proposal IS NULL",
                    (sid,)).fetchall():
                if from_owner and owner_marks.get(key, state) == "awaiting_others":
                    continue
                found = target(item)
                if found is None:
                    continue
                body = actions.make(sid, "remove_keyword", found, {"keyword": FOLLOW_UP_LABEL},
                                    reason="Someone answered." if not from_owner else "No longer awaiting a reply.",
                                    producer=f"follow-up:{sid}")
                intel.execute("UPDATE follow_ups SET cleared_proposal = ? WHERE source_id = ? AND thread_key = ? AND "
                              "last_item = ?", (_propose(config, body), sid, key, item))
                cleared += 1
            intel.commit()
        finally:
            intel.close()
            mail.close()
        if made or cleared:
            out.append({"source_id": sid, "step": "mail-follow-ups", "labelled": made, "cleared": cleared})
    return out


# -- reply labels -------------------------------------------------------------------------------------------------

def reply_labels_pending(config) -> list[dict]:
    from towpath.mail import analysis, threads

    out = []
    for sid in analysis.eligible(config):
        if not store_path(config.store_dir, "mailintel").exists():
            continue
        setting = reply_labels_setting(config.store_dir, sid)
        intel = open_store(config.store_dir, "mailintel", ROLE)
        mail = open_store(config.store_dir, "mail", ROLE)
        added = removed = 0
        try:
            desired = {}
            if setting["on"]:
                since = (datetime.fromisoformat(setting["since"]) - timedelta(days=REPLY_LABELS_WINDOW_DAYS))
                owner_marks = threads.marks(config.store_dir, sid)
                for key, state, item in intel.execute(
                        "SELECT thread_key, state, last_item FROM threads WHERE source_id = ? AND kind = "
                        "'conversation' AND last_date >= ?", (sid, since.isoformat())):
                    label = REPLY_LABELS.get(owner_marks.get(key, state))
                    if label:
                        desired[key] = (item, label)
            active = {(r[0], r[1], r[2]) for r in intel.execute(
                "SELECT thread_key, item, label FROM reply_labels WHERE source_id = ? AND cleared_proposal IS NULL",
                (sid,))}

            stamp = now().strftime("%Y-%m-%d %H:%M:%S")

            def propose(kind, item, label, reason, sid=sid, mail=mail, stamp=stamp):
                m = mail.execute("SELECT * FROM messages WHERE item_id = ?", (item,)).fetchone()
                if m is None:
                    return None
                # The time makes a label put back on (or taken off again) a new change, not a repeat of one done.
                return _propose(config, actions.make(sid, kind, actions.message_target(dict(m)), {"keyword": label},
                                                     reason=f"{reason} ({stamp} UTC)", producer=f"reply-labels:{sid}"))

            for key, item, label in sorted(active):
                if desired.get(key) == (item, label):
                    continue
                pid = propose("remove_keyword", item, label, "The conversation moved on.")
                intel.execute("UPDATE reply_labels SET cleared_proposal = ? WHERE source_id = ? AND thread_key = ? AND "
                              "item = ? AND label = ?", (pid or "gone", sid, key, item, label))
                removed += 1
            for key, (item, label) in desired.items():
                if (key, item, label) in active:
                    continue
                pid = propose("add_keyword", item, label, "Waiting for your reply." if label == "To-reply" else
                              "You're waiting for an answer.")
                if pid:
                    intel.execute("INSERT OR REPLACE INTO reply_labels VALUES (?,?,?,?,?,?,NULL)",
                                  (sid, key, item, label, pid, now().isoformat(timespec="seconds")))
                    added += 1
            intel.commit()
        finally:
            intel.close()
            mail.close()
        if added or removed:
            out.append({"source_id": sid, "step": "mail-reply-labels", "added": added, "removed": removed})
    return out
