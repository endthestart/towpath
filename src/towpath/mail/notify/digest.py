"""The digest message."""

import uuid
from contextlib import closing
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import format_datetime, formataddr
from zoneinfo import ZoneInfo

from towpath.mail import actions
from towpath.mail.notify.common import DIGEST_HOUR, ROLE, TIMEZONE, digest_setting, now, _propose
from towpath.stores import open_store, store_path


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
    from towpath.mail import purchases

    bought = [p for p in purchases.listing(store_dir, source_id, limit=20, role=role)
              if (p["day"] or "") >= since.date().isoformat()]
    if bought:
        lines += ["", "Bought:"]
        lines += [f"  - {p['merchant']}" + (f", {p['total']:.2f} {p['currency'] or ''}".rstrip()
                                            if p["total"] is not None else "") +
                  (f": {', '.join(p['items'][:3])}" if p["items"] else "") for p in bought]
    noisy = [s for s in senders.listing(store_dir, source_id, order="unread", limit=50, role=role)
             if s["unsubscribe"] and s["category"] in ("newsletter", "marketing") and s["unread"] >= 10][:5]
    if noisy:
        lines += ["", "Mostly unread — maybe unsubscribe:"]
        lines += [f"  - {s['name'] or s['address']}: {s['unread']} unread of {s['messages']}" for s in noisy]
    from towpath import life_today

    # This account's own mail and events, and photo folders, if allowed for the life story: nothing from another
    # account is ever put into this one's mailbox.
    from towpath import folders as folder_index

    open_to = life_today.allowed(store_dir)
    past = life_today.on_this_day(store_dir, role=role,
                                  sources=({source_id} | {folder_index.SID}) & open_to)["years"]
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
