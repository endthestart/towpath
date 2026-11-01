"""Reading contacts and events (any role): names, birthdays, the week ahead, and past events."""

import json
import re
from contextlib import closing

from towpath.stores import open_store, store_path


def names(store_dir, role: str = "worker") -> dict[str, str]:
    """Contact names by email address, from every contacts connection."""
    if store_dir is None or not store_path(store_dir, "dav").exists():
        return {}
    out = {}
    with closing(open_store(store_dir, "dav", role)) as db:
        for name, emails in db.execute("SELECT name, emails FROM contacts WHERE name IS NOT NULL"):
            for e in json.loads(emails):
                out.setdefault(e, name)
    return out


def birthdays_ahead(store_dir, days: int = 14, today=None, role: str = "web") -> list[dict]:
    """Contacts' birthdays, anniversaries and other dates their cards record, in the next ``days`` days (today
    included), soonest first, with the years they mark when the card gives a year. A 29 February date falls on the
    28th in other years."""
    from datetime import date, timedelta

    if store_dir is None or not store_path(store_dir, "dav").exists():
        return []
    today = today or _today()
    out, seen = [], set()
    with closing(open_store(store_dir, "dav", role)) as db:
        # the same person in two address books: a card with a year, then one with an address, wins
        rows = db.execute("SELECT * FROM (SELECT name, emails, birthday AS day, 'Birthday' AS label FROM contacts "
                          "WHERE birthday IS NOT NULL AND name IS NOT NULL UNION ALL SELECT c.name, c.emails, d.date, "
                          "d.label FROM contact_dates d JOIN contacts c USING (source_id, href) WHERE d.date IS NOT "
                          "NULL AND c.name IS NOT NULL) ORDER BY CAST(substr(day, 1, 4) AS INTEGER) <= 1900, "
                          "emails = '[]'")
        for name, emails, birthday, label in rows:
            m = re.fullmatch(r"(\d{4})?-*(\d{2})-(\d{2})", birthday)  # "1990-10-20", or "---10-20" with no year
            if not m:
                continue
            year, month, day = m.group(1) or "", int(m.group(2)), int(m.group(3))
            for y in (today.year, today.year + 1):
                try:
                    when = date(y, month, day)
                except ValueError:
                    if (month, day) != (2, 29):
                        break
                    when = date(y, 2, 28)
                if today <= when < today + timedelta(days=days):
                    # 1604 (Apple) and 1900 or so (other exporters) stand for "no year"
                    born = int(year) if year.isdigit() and 1900 < int(year) <= today.year else None
                    key = (name.casefold(), when, label)
                    if key not in seen:
                        seen.add(key)
                        out.append({"name": name, "date": when, "in_days": (when - today).days, "label": label,
                                    "turns": when.year - born if born else None,
                                    "address": (json.loads(emails) or [None])[0]})
                    break
    return sorted(out, key=lambda b: (b["date"], b["name"]))


def _today():
    """Today where the owner lives (the server's own clock may be on UTC)."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from towpath.mail.notify import TIMEZONE

    return datetime.now(ZoneInfo(TIMEZONE)).date()


def _local(value: str | None, all_day: bool, tz: str | None, zone):
    """A stored start or end as the owner's local datetime (an all-day date at midnight)."""
    from datetime import date, datetime
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    if not value:
        return None
    try:
        if all_day or len(value) == 10:
            return datetime.combine(date.fromisoformat(value[:10]), datetime.min.time(), zone)
        if value.endswith("Z"):
            return datetime.fromisoformat(value[:-1] + "+00:00").astimezone(zone)
        try:
            source = ZoneInfo(tz) if tz and tz != "UTC" else zone  # no zone: the calendar's floating local time
        except (ZoneInfoNotFoundError, ValueError):
            source = zone
        return datetime.fromisoformat(value).replace(tzinfo=source).astimezone(zone)
    except ValueError:
        return None


def week_ahead(store_dir, today=None, days: int = 7, limit: int = 12, role: str = "web", now=None) -> list[dict]:
    """What's on in the next ``days`` days (today included), in the owner's own time zone, soonest first, across
    calendars: events that don't repeat, changed occurrences of ones that do, and events already under way (a
    trip that started yesterday). Cancelled events and events already over are left out. A repeating event is
    stored once, as its first occurrence, so its other occurrences aren't listed yet."""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    from towpath.mail.notify import TIMEZONE

    if store_dir is None or not store_path(store_dir, "dav").exists():
        return []
    zone = ZoneInfo(TIMEZONE)
    today = today or _today()
    now = now or datetime.now(zone)
    start_of = datetime.combine(today, datetime.min.time(), zone)
    end_of = start_of + timedelta(days=days)
    early, late = (today - timedelta(days=1)).isoformat(), (today + timedelta(days=days)).isoformat()
    with closing(open_store(store_dir, "dav", role)) as db:
        rows = db.execute(
            "SELECT summary, start, end, all_day, tz, location FROM events WHERE start IS NOT NULL AND "
            "(recurring = 0 OR override = 1) AND coalesce(upper(status), '') != 'CANCELLED' AND "
            "((start >= ? AND start < ?) OR (start < ? AND end IS NOT NULL AND end >= ?))",
            (early, late + "~", early, early)).fetchall()
    found = {}
    for summary, start, end, all_day, tz, location in rows:
        begins = _local(start, bool(all_day), tz, zone)
        if begins is None:
            continue
        ends = _local(end, bool(all_day), tz, zone)
        if ends is None or ends <= begins:
            ends = begins + (timedelta(days=1) if all_day else timedelta(minutes=1))
        if begins >= end_of or ends <= start_of or (not all_day and ends <= now):
            continue  # outside the window, or already over
        key = (summary or "", begins)
        if key in found and (found[key]["location"] or not location):
            continue  # the same event in two calendars: keep the copy with a place
        found[key] = {"summary": summary or "(No title)", "day": begins.date(), "starts": begins,
                      "time": None if all_day else begins.strftime("%H:%M"), "location": location,
                      "until": (ends - timedelta(days=1)).date() if all_day and ends - begins > timedelta(days=1)
                      else None}
    return sorted(found.values(), key=lambda e: (e["starts"], e["summary"]))[:limit]


def events(store_dir, source_id: str | None = None, before: str | None = None, role: str = "web") -> list[dict]:
    if not store_path(store_dir, "dav").exists():
        return []
    where, args = ["start IS NOT NULL"], []
    if source_id:
        where.append("source_id = ?")
        args.append(source_id)
    if before:
        where.append("start < ?")
        args.append(before)
    with closing(open_store(store_dir, "dav", role)) as db:
        rows = [dict(r) for r in db.execute(f"SELECT * FROM events WHERE {' AND '.join(where)} ORDER BY start DESC",
                                            args)]
    for r in rows:
        r["attendees"] = json.loads(r["attendees"])
    return rows
