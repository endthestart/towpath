"""Contacts and calendars connections: test, add, and sync (connector only, like towpath.connections).

A connection (adapter ``dav``) holds one app password that can read both CardDAV and CalDAV; Fastmail's servers are
filled in, and iCloud's are found from its standard starting addresses. Syncing lists every address book and
calendar, compares ETags, reads only what changed with a multiget, and removes what a complete listing no longer
shows. The owner starts it, or puts it on a schedule, on the Connections page, as for mail.
"""

import json
import re
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from towpath import connections
from towpath.adapters import dav
from towpath.connections import ConnectionProblem, _update, _write_secret, secret_path
from towpath.stores import open_store, store_path

ROLE = "connect"
PROVIDERS = {
    "fastmail_dav": {"label": "Fastmail contacts and calendars", "service": "Fastmail",
                     "help_url": "https://www.fastmail.help/hc/en-us/articles/360058752854-App-passwords"},
    "icloud_dav": {"label": "iCloud contacts and calendars", "service": "iCloud",
                   "help_url": "https://support.apple.com/en-us/102654"}}
PROVIDER = PROVIDERS["fastmail_dav"]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def homes(username: str, contacts_url: str | None = None, calendars_url: str | None = None) -> dict:
    return {"contacts": contacts_url or dav.FASTMAIL["contacts"].format(user=username),
            "calendars": calendars_url or dav.FASTMAIL["calendars"].format(user=username)}


def discover(username: str, password: str, bases: dict, opener=None) -> dict:
    """The homes for a server found the standard way (iCloud), from its starting addresses."""
    from towpath.adapters.errors import AuthStop, ServerStop

    client = dav.DavClient(username, password, opener)
    try:
        return {kind: client.discover(base, kind) for kind, base in bases.items()}
    except AuthStop:
        raise ConnectionProblem("The server rejected the address or password. Use an app-specific password.") \
            from None
    except (ServerStop, dav.DavViolation) as exc:
        raise ConnectionProblem(f"Couldn't find the contacts and calendars ({exc}).") from None


def test(username: str, password: str, urls: dict | None = None, opener=None) -> list[dict]:
    """Sign in and list the address books and calendars, or raise ConnectionProblem in plain words."""
    from towpath.adapters.errors import AuthStop, ServerStop

    if "@" not in username:
        raise ConnectionProblem("Enter your full email address.")
    if not password:
        raise ConnectionProblem("Enter the app password.")
    client = dav.DavClient(username, password, opener)
    found = []
    try:
        for kind, home in (urls or homes(username)).items():
            found += [{**c, "kind": kind} for c in client.collections(home, kind)]
    except AuthStop:
        raise ConnectionProblem("The server rejected the address or password. Use an app password that can read "
                                "contacts and calendars.") from None
    except (ServerStop, dav.DavViolation) as exc:
        raise ConnectionProblem(f"Couldn't read contacts and calendars ({exc}).") from None
    if not found:
        raise ConnectionProblem("The account has no address books or calendars Towpath can read.")
    return found


def add(store_dir: Path, credentials_dir: Path, username: str, password: str, found: list[dict],
        urls: dict | None = None, taken: set[str] = frozenset(), provider: str = "fastmail_dav") -> str:
    sid = connections._new_id(store_dir, "contacts-calendars", set(taken))
    _write_secret(secret_path(credentials_dir, sid), password)
    settings = {"username": username, "homes": urls or homes(username)}
    now_ = now()
    folders = [{"name": c["name"], "kind": c["kind"], "href": c["href"]} for c in found]
    with closing(open_store(store_dir, "connections", ROLE)) as db:
        db.execute("""INSERT INTO connections (source_id, provider, adapter, display_name, settings, folders, state,
                      indexing, created_at, updated_at)
                      VALUES (?, ?, 'dav', ?, ?, ?, 'ready', 'idle', ?, ?)""",
                   (sid, provider, PROVIDERS[provider]["label"], json.dumps(settings), json.dumps(folders), now_,
                    now_))
        db.execute("INSERT INTO connection_events (source_id, at, event) VALUES (?, ?, 'added')", (sid, now_))
        db.commit()
    return sid


def _store_items(db, sid: str, collection: dict, texts: dict[str, str], etags: dict[str, str]) -> int:
    count = 0
    for href, text in texts.items():
        if collection["kind"] == "contacts":
            card = dav.parse_vcard(text)
            db.execute("INSERT OR REPLACE INTO contacts VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                       (sid, href, etags.get(href, ""), card["uid"], card["name"], json.dumps(card["emails"]),
                        json.dumps(card["phones"]), card["birthday"], card["org"], collection["href"], now()))
            db.execute("DELETE FROM contact_dates WHERE source_id = ? AND href = ?", (sid, href))
            db.executemany("INSERT INTO contact_dates VALUES (?,?,?,?)",
                           [(sid, href, label, day) for label, day in card["dates"]] or [(sid, href, None, None)])
        else:
            db.execute("DELETE FROM events WHERE source_id = ? AND href = ?", (sid, href))
            for n, e in enumerate(dav.parse_events(text)):
                db.execute("INSERT INTO events (source_id, href, n, etag, uid, summary, start, end, all_day, tz, "
                           "location, organizer, attendees, recurring, description, collection, updated_at, override, "
                           "status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           (sid, href, n, etags.get(href, ""), e["uid"], e["summary"], e["start"], e["end"],
                            int(e["all_day"]), e["tz"], e["location"], e["organizer"], json.dumps(e["attendees"]),
                            int(e["recurring"] or bool(e["recurrence_id"])), e["description"], collection["href"],
                            now(), int(bool(e["recurrence_id"])), e["status"]))
        count += 1
    return count


def sync(store_dir: Path, credentials_dir: Path, c: dict, opener=None) -> dict:
    """Bring one connection's contacts and events up to date. Returns counts."""
    sid = c["source_id"]
    password = secret_path(credentials_dir, sid).read_text()
    client = dav.DavClient(c["settings"]["username"], password, opener)
    totals = {"contacts": 0, "events": 0, "read": 0, "removed": 0}
    with closing(open_store(store_dir, "dav", ROLE)) as db:
        for kind, home in c["settings"]["homes"].items():
            for col in client.collections(home, kind):
                col["kind"] = kind
                etags = client.etags(col["href"])
                table = "contacts" if kind == "contacts" else "events"
                known = {r[0]: r[1] for r in db.execute(f"SELECT DISTINCT href, etag FROM {table} WHERE source_id = ? "
                                                        f"AND collection = ?", (sid, col["href"]))}
                checked = {r[0] for r in db.execute(
                    "SELECT DISTINCT href FROM contact_dates WHERE source_id = ?" if kind == "contacts" else
                    "SELECT DISTINCT href FROM events WHERE source_id = ? AND override IS NOT NULL", (sid,))}
                # a card read before Towpath kept its other dates, or an event before it kept whether it was a
                # changed occurrence and its status, is read once more
                changed = [h for h, e in etags.items() if known.get(h) != e or h not in checked]
                totals["read"] += _store_items(db, sid, col, client.multiget(col["href"], changed, kind), etags)
                gone = [h for h in known if h not in etags]
                db.executemany(f"DELETE FROM {table} WHERE source_id = ? AND href = ?", [(sid, h) for h in gone])
                if kind == "contacts":
                    db.executemany("DELETE FROM contact_dates WHERE source_id = ? AND href = ?",
                                   [(sid, h) for h in gone])
                totals["removed"] += len(gone)
                db.execute("INSERT OR REPLACE INTO collections VALUES (?,?,?,?,?,?,?)",
                           (sid, col["href"], kind, col["name"], col["ctag"], len(etags), now()))
                db.commit()
        totals["contacts"] = db.execute("SELECT count(*) FROM contacts WHERE source_id = ?", (sid,)).fetchone()[0]
        totals["events"] = db.execute("SELECT count(*) FROM events WHERE source_id = ?", (sid,)).fetchone()[0]
    return totals


def sync_pending(config, credentials_dir: Path, opener=None) -> list[dict]:
    """Sync each contacts-and-calendars connection the owner started or scheduled."""
    from towpath.adapters.errors import SyncStop

    out = []
    for c in connections.all_connections(config.store_dir):
        if c["adapter"] != "dav" or c["state"] != "ready" or c["indexing"] not in ("requested", "running"):
            continue
        sid = c["source_id"]
        _update(config.store_dir, sid, "indexing-started", indexing="running")
        try:
            totals = sync(config.store_dir, credentials_dir, c, opener)
        except SyncStop as exc:
            _update(config.store_dir, sid, "indexing-stopped", {"termination": exc.code}, indexing="idle",
                    last_error=connections._plain_stop(exc.code, exc.reason))
            out.append({"source_id": sid, "termination": exc.code})
            continue
        _update(config.store_dir, sid, "indexed", totals, indexing="idle", last_error=None,
                progress={"phase": "finished", "processed": totals["read"], "rate": None,
                          "indexed": totals["contacts"] + totals["events"], "at": now()})
        out.append({"source_id": sid, "step": "contacts-calendars", **totals})
    return out


# -- reading (any role) ---------------------------------------------------------------------------------------------

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
