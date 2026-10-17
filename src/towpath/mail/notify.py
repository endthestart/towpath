"""Telling the owner about mail, inside the mailbox: important-mail alerts, a digest, and statistics.

- **Alerts.** When a conversation with a person newly needs the owner's reply, its latest message is flagged (or
  given the label "Important"), so the owner's mail app, which already notifies on flagged mail or that label if they
  set it up, tells them. Only mail that arrives after alerts are turned on.
- **Digest.** Daily or weekly, a short message put straight into the inbox (APPEND; nothing is sent): who is waiting
  for a reply, what the owner is waiting on, new mail by category, rule activity, and senders worth unsubscribing
  from.
- **Follow-up reminders.** When the owner wrote last and nobody has answered after a few days, the owner's message
  gets the label "Follow-up" (and the worker drafts a nudge); the label comes off when someone answers. Only
  conversations the owner wrote in during the month before the reminder would be due.
- **Statistics.** Mail received and sent per week, how quickly the owner replies, and the people they write to most.

Alerts and digests are ordinary mail changes proposed by the worker and approved by the owner's own setting, so they
are dry runs while the account is in dry runs only or the kind of change (flag, label, note) is turned off.
"""

import json
import statistics
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import format_datetime, formataddr
from zoneinfo import ZoneInfo

from towpath.canonical import digest
from towpath.mail import actions, settings
from towpath.stores import open_store, store_path

ROLE = "worker"
ALERT_MODES = {"off": "Off", "flag": "Flag the message", "keyword": "Label it “Important”"}
DIGEST_EVERY = {"off": "Off", "daily": "Every morning", "weekly": "Monday mornings"}
FOLLOW_UP_DAYS = {0: "Off", 3: "After 3 days", 7: "After a week", 14: "After two weeks"}
FOLLOW_UP_LABEL = "Follow-up"
FOLLOW_UP_WINDOW_DAYS = 30
TIMEZONE = "America/Chicago"
DIGEST_HOUR = 7


def now() -> datetime:
    return datetime.now(timezone.utc)


# -- the owner's settings (web role) ------------------------------------------------------------------------------

def alert_setting(store_dir, source_id: str, role: str = ROLE) -> dict:
    return settings._get(store_dir, source_id, "alerts", role) or {"mode": "off"}


def set_alerts(store_dir, source_id: str, mode: str, author: str = "owner") -> None:
    if mode not in ALERT_MODES:
        raise ValueError("unknown alert mode")
    settings._set(store_dir, source_id, "alerts", {"mode": mode, "since": now().isoformat(timespec="seconds")},
                  "mail-alerts", author)


def digest_setting(store_dir, source_id: str, role: str = ROLE) -> dict:
    return settings._get(store_dir, source_id, "digest", role) or {"every": "off"}


def set_digest(store_dir, source_id: str, every: str, author: str = "owner") -> None:
    if every not in DIGEST_EVERY:
        raise ValueError("unknown digest frequency")
    settings._set(store_dir, source_id, "digest", {"every": every, "since": now().isoformat(timespec="seconds")},
                  "mail-digest", author)


def follow_up_days(store_dir, source_id: str, role: str = ROLE) -> int:
    return int((settings._get(store_dir, source_id, "follow_up", role) or {}).get("days", 0))


def set_follow_up(store_dir, source_id: str, days: int, author: str = "owner") -> None:
    if days not in FOLLOW_UP_DAYS:
        raise ValueError("unknown follow-up delay")
    settings._set(store_dir, source_id, "follow_up", {"days": days}, "mail-follow-up", author)


def auto_approved(store_dir, body: dict, role: str = "act") -> bool:
    """An alert, digest or follow-up label the owner's current setting asked for."""
    producer = str(body.get("producer", ""))
    source = body.get("source_id")
    if producer == f"follow-up:{source}":
        return body.get("type") in ("add_keyword", "remove_keyword") and \
            body.get("params", {}).get("keyword") == FOLLOW_UP_LABEL and follow_up_days(store_dir, source, role) > 0
    if producer == f"alert:{source}":
        mode = alert_setting(store_dir, source, role)["mode"]
        return (mode, body.get("type")) in (("flag", "flag"), ("keyword", "add_keyword"))
    if producer.startswith(f"digest:{source}:"):
        return body.get("type") == "inbox_note" and digest_setting(store_dir, source, role)["every"] != "off"
    return False


def _propose(config, body: dict) -> str:
    pid = actions.proposal_id(body)
    with closing(open_store(config.store_dir, "mailplan", ROLE)) as plan:
        plan.execute("INSERT OR IGNORE INTO proposals (proposal_id, source_id, body, digest, created_at) "
                     "VALUES (?,?,?,?,?)", (pid, body["source_id"], json.dumps(body, sort_keys=True), digest(body),
                                            now().isoformat(timespec="seconds")))
        plan.commit()
    return pid


# -- alerts -------------------------------------------------------------------------------------------------------

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
            # Someone answered (or the owner marked it otherwise): take the label off again.
            for key, item in intel.execute(
                    "SELECT f.thread_key, f.last_item FROM follow_ups f JOIN threads t USING (source_id, thread_key) "
                    "WHERE f.source_id = ? AND f.cleared_proposal IS NULL AND t.last_from_owner = 0",
                    (sid,)).fetchall():
                found = target(item)
                if found is None:
                    continue
                body = actions.make(sid, "remove_keyword", found, {"keyword": FOLLOW_UP_LABEL},
                                    reason="Someone answered.", producer=f"follow-up:{sid}")
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


# -- digest -------------------------------------------------------------------------------------------------------

def _period(every: str, at: datetime) -> str | None:
    """The digest period that is due at ``at`` (local time), or None before the morning hour."""
    local = at.astimezone(ZoneInfo(TIMEZONE))
    if local.hour < DIGEST_HOUR:
        return None
    if every == "daily":
        return local.strftime("%Y-%m-%d")
    if every == "weekly":
        monday = local - timedelta(days=local.weekday())
        return "week-" + monday.strftime("%Y-%m-%d")
    return None


def digest_text(store_dir, source_id: str, since: datetime, role: str = ROLE) -> str:
    from towpath.mail import senders, threads

    lines = [f"Towpath digest for {datetime.now(ZoneInfo(TIMEZONE)).strftime('%A %d %B %Y')}", ""]
    waiting = threads.listing(store_dir, source_id, "needs_owner_reply", limit=15, role=role)
    lines.append(f"Waiting for your reply ({len(waiting)}{'+' if len(waiting) == 15 else ''}):")
    lines += [f"  - {t['subject'] or '(no subject)'} — {', '.join(t['participants'][:2])} "
              f"({(t['last_date'] or '')[:10]})" for t in waiting] or ["  nobody"]
    awaiting = threads.listing(store_dir, source_id, "awaiting_others", limit=10, role=role)
    lines += ["", f"You're waiting on ({len(awaiting)}{'+' if len(awaiting) == 10 else ''}), longest first:"]

    def days(t):
        return max(0, (now() - datetime.fromisoformat(t["last_date"])).days) if t["last_date"] else 0

    lines += [f"  - {t['subject'] or '(no subject)'} — {', '.join(t['participants'][:2])}, no answer for "
              f"{days(t)} day{'s' if days(t) != 1 else ''}" for t in sorted(awaiting, key=days, reverse=True)] or \
        ["  nothing"]
    cats = senders.categories(store_dir, source_id, role)
    counts: dict[str, int] = {}
    if store_path(store_dir, "mail").exists():
        with closing(open_store(store_dir, "mail", role)) as db:
            for m in db.execute("SELECT subject, from_addr, list_id FROM messages WHERE source_id = ? AND "
                                "upper(mailbox) = 'INBOX' AND date_utc >= ?", (source_id, since.isoformat())):
                c = senders.message_category(dict(m), cats.get(m["from_addr"]))
                counts[c] = counts.get(c, 0) + 1
    lines += ["", "New in your inbox:"]
    lines += [f"  {senders.CATEGORIES.get(c, c)}: {n}" for c, n in sorted(counts.items(), key=lambda x: -x[1])] or \
        ["  nothing new"]
    noisy = [s for s in senders.listing(store_dir, source_id, order="unread", limit=50, role=role)
             if s["unsubscribe"] and s["category"] in ("newsletter", "marketing") and s["unread"] >= 10][:5]
    if noisy:
        lines += ["", "Mostly unread — maybe unsubscribe:"]
        lines += [f"  - {s['name'] or s['address']}: {s['unread']} unread of {s['messages']}" for s in noisy]
    from towpath import life_today

    past = life_today.on_this_day(store_dir, role=role)["years"]
    if past:
        lines += ["", "On this day:"]
        for y in past[:5]:
            bits = [c.statement for c in y["events"][:2]]
            if y["photos"]:
                bits.append(f"{len(y['photos'])} photo{'s' if len(y['photos']) != 1 else ''}")
            if y["sent"]:
                bits.append(f"you wrote to {y['sent'][0]['to']}")
            lines.append(f"  {y['year']}: " + "; ".join(bits))
    lines += ["", "Open Towpath's Mail page to answer, clean up or unsubscribe."]
    return "\n".join(lines)


def digest_message(store_dir, source_id: str, text: str, role: str = ROLE) -> str:
    from towpath.mail import threads

    with closing(open_store(store_dir, "mail", role)) as mail:
        me = threads.owner(store_dir, source_id, mail, role)
    address = me["address"] or "owner@localhost"
    m = EmailMessage()
    m["From"] = formataddr(("Towpath digest", address))
    m["To"] = address
    m["Subject"] = text.splitlines()[0]
    m["Date"] = format_datetime(now())
    m["Message-ID"] = f"<towpath-digest-{uuid.uuid4().hex}@{address.split('@')[-1]}>"
    m["Auto-Submitted"] = "auto-generated"
    m.set_content(text + "\n")
    return m.as_string()


def digests_pending(config, at: datetime | None = None) -> list[dict]:
    from towpath.mail import analysis

    at = at or now()
    out = []
    for sid in analysis.eligible(config):
        setting = digest_setting(config.store_dir, sid)
        period = _period(setting["every"], at) if setting["every"] != "off" else None
        if period is None:
            continue
        with closing(open_store(config.store_dir, "mailintel", ROLE)) as intel:
            if intel.execute("SELECT 1 FROM digests WHERE source_id = ? AND period = ?", (sid, period)).fetchone():
                continue
            since = at - timedelta(days=7 if setting["every"] == "weekly" else 1)
            text = digest_text(config.store_dir, sid, since)
            body = actions.make(sid, "inbox_note", {"mailbox_role": "inbox"},
                                {"message": digest_message(config.store_dir, sid, text)},
                                reason=f"Your {setting['every']} digest", producer=f"digest:{sid}:{period}")
            intel.execute("INSERT INTO digests VALUES (?,?,?,?)", (sid, period, _propose(config, body),
                                                                    now().isoformat(timespec="seconds")))
            intel.commit()
        out.append({"source_id": sid, "step": "mail-digest", "period": period})
    return out


# -- statistics ---------------------------------------------------------------------------------------------------

def measure(store_dir, source_id: str, role: str = ROLE) -> dict:
    """Mail statistics from the mail store, stored for the Statistics page."""
    from towpath.mail import threads

    with closing(open_store(store_dir, "mail", role)) as mail:
        me = threads.owner(store_dir, source_id, mail, role)
        sent = sorted(me["sent"])
        marks = ",".join("?" for _ in sent)
        start = (now() - timedelta(weeks=52)).isoformat()
        weeks: dict[str, list[int]] = {}
        for week, is_sent, n in mail.execute(
                f"SELECT strftime('%Y-%W', date_utc), mailbox IN ({marks}), count(*) FROM messages WHERE source_id = ? "
                f"AND date_utc >= ? GROUP BY 1, 2", (*sent, source_id, start)):
            weeks.setdefault(week, [0, 0])[1 if is_sent else 0] += n
        correspondents: dict[str, int] = {}
        for (to,) in mail.execute(f"SELECT to_addrs FROM messages WHERE source_id = ? AND mailbox IN ({marks}) AND "
                                  f"date_utc >= ?", (source_id, *sent, start)):
            for a in json.loads(to):
                correspondents[a] = correspondents.get(a, 0) + 1
        delays = []
        recent = (now() - timedelta(days=90)).isoformat()
        keys = [r[0] for r in mail.execute(f"SELECT DISTINCT thread_key FROM messages WHERE source_id = ? AND "
                                           f"mailbox IN ({marks}) AND date_utc >= ?", (source_id, *sent, recent))]
        for key in keys:
            msgs = threads._messages(mail, source_id, key)
            for before, after in zip(msgs, msgs[1:], strict=False):
                mine_after = after["from_addr"] in me["addresses"] or after["mailbox"] in me["sent"]
                mine_before = before["from_addr"] in me["addresses"] or before["mailbox"] in me["sent"]
                if mine_after and not mine_before and before["date_utc"] and after["date_utc"]:
                    hours = (datetime.fromisoformat(after["date_utc"]) -
                             datetime.fromisoformat(before["date_utc"])).total_seconds() / 3600
                    if hours >= 0:
                        delays.append(hours)
    body = {"weeks": sorted(([w, *v] for w, v in weeks.items()), reverse=True),
            "top_correspondents": sorted(correspondents.items(), key=lambda x: -x[1])[:15],
            "reply_hours": {"median": round(statistics.median(delays), 1) if delays else None,
                            "count": len(delays)}}
    with closing(open_store(store_dir, "mailintel", ROLE)) as intel:
        intel.execute("INSERT OR REPLACE INTO stats VALUES (?,?,?)", (source_id, json.dumps(body),
                                                                       now().isoformat(timespec="seconds")))
        intel.commit()
    return body


def stats(store_dir, source_id: str, role: str = "web") -> dict | None:
    if not store_path(store_dir, "mailintel").exists():
        return None
    with closing(open_store(store_dir, "mailintel", role)) as db:
        row = db.execute("SELECT body, updated_at FROM stats WHERE source_id = ?", (source_id,)).fetchone()
    return {**json.loads(row[0]), "updated_at": row[1]} if row else None
