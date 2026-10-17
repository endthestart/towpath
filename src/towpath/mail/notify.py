"""Telling the owner about mail, inside the mailbox: important-mail alerts, a digest, and statistics.

- **Alerts.** When a conversation with a person newly needs the owner's reply, its latest message is flagged (or
  given the label "Important"), so the owner's mail app, which already notifies on flagged mail or that label if they
  set it up, tells them. Only mail that arrives after alerts are turned on.
- **Digest.** Daily or weekly, a short message put straight into the inbox (APPEND; nothing is sent): who is waiting
  for a reply, what the owner is waiting on, new mail by category, rule activity, and senders worth unsubscribing
  from.
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


def auto_approved(store_dir, body: dict, role: str = "act") -> bool:
    """An alert or digest the owner's current setting asked for."""
    producer = str(body.get("producer", ""))
    source = body.get("source_id")
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
    lines += ["", f"You're waiting on ({len(awaiting)}{'+' if len(awaiting) == 10 else ''}):"]
    lines += [f"  - {t['subject'] or '(no subject)'} — {', '.join(t['participants'][:2])}" for t in awaiting] or \
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
