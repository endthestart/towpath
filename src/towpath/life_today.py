"""On this day, and each year at a glance: the owner's records from the same date in earlier years, and a year's
photos, mail and the people written to most.

Read-only views over stores other jobs keep: life-story events, photo metadata and mail management's mail store.
"""

import json
from contextlib import closing
from datetime import date

from towpath import life
from towpath.stores import open_store, store_path


def allowed(store_dir) -> set[str]:
    """The accounts and folders the owner allows for the life story."""
    from towpath.unified import grants

    return {sid for sid, f in grants.granted(store_dir).items() if "life-evidence" in f}


def on_this_day(store_dir, day: date | None = None, role: str = "web", sources: set[str] | None = None) -> dict:
    """Events, photos and the owner's own mail from this month and day in earlier years, newest year first, from
    ``sources`` only (by default, those allowed for the life story)."""
    from towpath.photos import is_private

    sources = allowed(store_dir) if sources is None else sources
    day = day or date.today()
    md = day.strftime("%m-%d")
    events = [c for c in life.claims(store_dir, limit=10**6, role=role)
              if c.state != "rejected" and (c.when.earliest or "")[5:10] == md and
              (c.when.earliest or "")[:4] < str(day.year) and c.significance != "routine" and
              (not c.citations or any(cit.ref.partition(":")[0] in sources for cit in c.citations))]
    photos = []
    if store_path(store_dir, "files").exists():
        with closing(open_store(store_dir, "files", role)) as db:
            photos = [dict(r) for r in db.execute(
                "SELECT p.taken, p.model, p.occurrence_id, o.provider_id, o.root_alias, o.rel_path FROM photo_meta p "
                "JOIN occurrences o "
                "USING (occurrence_id) WHERE substr(p.taken, 6, 5) = ? AND substr(p.taken, 1, 4) < ? AND "
                "o.missing_since_run IS NULL AND p.occurrence_id NOT IN (SELECT occurrence_id FROM photo_hidden) "
                "ORDER BY "
                "p.taken DESC LIMIT 60", (md, str(day.year)))
                if r["provider_id"] in sources and not is_private(r["rel_path"])]
    sent = []
    if store_path(store_dir, "mail").exists():
        from towpath.mail import threads

        with closing(open_store(store_dir, "mail", role)) as db:
            for sid in [r[0] for r in db.execute("SELECT DISTINCT source_id FROM messages")]:
                if sid not in sources:
                    continue
                boxes = sorted(threads.sent_mailboxes(store_dir, sid, role))
                marks = ",".join("?" for _ in boxes)
                sent += [dict(r) for r in db.execute(
                    f"SELECT item_id, subject, to_addrs, date_utc FROM messages WHERE source_id = ? AND mailbox IN "
                    f"({marks}) AND substr(date_utc, 6, 5) = ? AND substr(date_utc, 1, 4) < ? ORDER BY date_utc DESC "
                    f"LIMIT 30", (sid, *boxes, md, str(day.year)))]
    for m in sent:
        m["to"] = ", ".join(json.loads(m.pop("to_addrs"))[:3])
    years = sorted({(c.when.earliest or "")[:4] for c in events} | {p["taken"][:4] for p in photos} |
                   {(m["date_utc"] or "")[:4] for m in sent}, reverse=True)
    return {"day": day, "years": [{"year": y,
                                   "events": [c for c in events if (c.when.earliest or "").startswith(y)],
                                   "photos": [p for p in photos if p["taken"].startswith(y)],
                                   "sent": [m for m in sent if (m["date_utc"] or "").startswith(y)]}
                                  for y in years if y]}


def year_context(store_dir, role: str = "web", sources: set[str] | None = None) -> dict[str, dict]:
    """Per year: photos taken, mail received and sent, and the people written to most, from ``sources`` only (by
    default, those allowed for the life story)."""
    sources = allowed(store_dir) if sources is None else sources
    out: dict[str, dict] = {}
    if store_path(store_dir, "files").exists():
        with closing(open_store(store_dir, "files", role)) as db:
            marks = ",".join("?" for _ in sources) or "''"
            for year, n in db.execute(f"SELECT substr(p.taken, 1, 4), count(*) FROM photo_meta p JOIN occurrences o "
                                      f"USING (occurrence_id) WHERE p.taken IS NOT NULL AND "
                                      f"o.provider_id IN ({marks}) AND "
                                      f"p.occurrence_id NOT IN (SELECT occurrence_id FROM photo_hidden) "
                                      f"GROUP BY 1", sorted(sources)):
                out.setdefault(year, {})["photos"] = n
    if store_path(store_dir, "mail").exists():
        from towpath.mail import threads

        with closing(open_store(store_dir, "mail", role)) as db:
            for sid in [r[0] for r in db.execute("SELECT DISTINCT source_id FROM messages")]:
                if sid not in sources:
                    continue
                boxes = sorted(threads.sent_mailboxes(store_dir, sid, role))
                marks = ",".join("?" for _ in boxes)
                for year, is_sent, n in db.execute(
                        f"SELECT substr(date_utc, 1, 4), mailbox IN ({marks}), count(*) FROM messages WHERE "
                        f"source_id = ? AND date_utc IS NOT NULL GROUP BY 1, 2", (*boxes, sid)):
                    key = "sent" if is_sent else "received"
                    out.setdefault(year, {})[key] = out.get(year, {}).get(key, 0) + n
                people: dict[tuple, int] = {}
                for year, to in db.execute(f"SELECT substr(date_utc, 1, 4), to_addrs FROM messages WHERE source_id = ? "
                                           f"AND mailbox IN ({marks}) AND date_utc IS NOT NULL", (sid, *boxes)):
                    for a in json.loads(to):
                        people[(year, a)] = people.get((year, a), 0) + 1
                for (year, a), _ in sorted(people.items(), key=lambda x: -x[1]):
                    top = out.setdefault(year, {}).setdefault("people", [])
                    if len(top) < 3:
                        top.append(a)
    return out


MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def one_year(store_dir, year: str, role: str = "web", sources: set[str] | None = None) -> dict:
    """A year in full: events by month, photos and mail per month, the busiest photo days, cameras, the people
    written to most, and the people written to for the first time. ``sources`` limits photos and mail to those
    connections; by default, those the owner allowed for the life story."""
    sources = allowed(store_dir) if sources is None else sources
    months = {f"{m:02d}": {"label": MONTHS[m - 1], "events": [], "photos": 0, "received": 0, "sent": 0}
              for m in range(1, 13)}
    events = sorted((c for c in life.claims(store_dir, limit=10**6, role=role)
                     if c.state != "rejected" and (c.when.earliest or "").startswith(year)),
                    key=lambda c: c.when.earliest or "")
    undated_month = []
    for c in events:
        month = (c.when.earliest or "")[5:7]
        (months[month]["events"] if month in months and c.when.precision != "year" else undated_month).append(c)
    days, cameras = [], []
    if store_path(store_dir, "files").exists():
        with closing(open_store(store_dir, "files", role)) as db:
            only, args = "", ()
            if sources is not None:  # always, now; kept as a guard
                marks = ",".join("?" for _ in sources) or "''"
                only = (f" AND occurrence_id IN (SELECT occurrence_id FROM occurrences WHERE provider_id IN ({marks}) "
                        f"AND rel_path NOT LIKE '%Exports/Hidden%')")
                args = tuple(sorted(sources))
            only += " AND occurrence_id NOT IN (SELECT occurrence_id FROM photo_hidden)"  # hidden in Apple Photos
            for month, n in db.execute(f"SELECT substr(taken, 6, 2), count(*) FROM photo_meta WHERE substr(taken, 1, "
                                       f"4) = ?{only} GROUP BY 1", (year, *args)):
                if month in months:
                    months[month]["photos"] = n
            days = [tuple(r) for r in db.execute(
                f"SELECT substr(taken, 1, 10), count(*) FROM photo_meta WHERE substr(taken, 1, 4) = ?{only} GROUP BY 1 "
                "ORDER BY 2 DESC LIMIT 8", (year, *args))]
            cameras = [tuple(r) for r in db.execute(
                "SELECT trim(coalesce(make, '') || ' ' || model), count(*) FROM photo_meta WHERE substr(taken, 1, 4) "
                f"= ? AND model IS NOT NULL{only} GROUP BY 1 ORDER BY 2 DESC LIMIT 5", (year, *args))]
    people: dict[str, int] = {}
    first: list[tuple[str, str]] = []
    if store_path(store_dir, "mail").exists():
        from towpath.mail import threads

        with closing(open_store(store_dir, "mail", role)) as db:
            for sid in [r[0] for r in db.execute("SELECT DISTINCT source_id FROM messages")]:
                if sources is not None and sid not in sources:
                    continue
                boxes = sorted(threads.sent_mailboxes(store_dir, sid, role))
                marks = ",".join("?" for _ in boxes)
                for month, is_sent, n in db.execute(
                        f"SELECT substr(date_utc, 6, 2), mailbox IN ({marks}), count(*) FROM messages WHERE "
                        f"source_id = ? AND substr(date_utc, 1, 4) = ? GROUP BY 1, 2", (*boxes, sid, year)):
                    if month in months:
                        months[month]["sent" if is_sent else "received"] += n
                seen_before: set[str] = set()
                for to, when in db.execute(f"SELECT to_addrs, date_utc FROM messages WHERE source_id = ? AND mailbox "
                                           f"IN ({marks}) AND date_utc < ? ORDER BY date_utc", (sid, *boxes,
                                                                                               f"{int(year) + 1}")):
                    for a in json.loads(to):
                        if when.startswith(year):
                            people[a] = people.get(a, 0) + 1
                            if a not in seen_before:
                                first.append((when[:10], a))
                        seen_before.add(a)
    return {"year": year, "months": list(months.values()), "events": events, "year_events": undated_month,
            "days": days, "cameras": cameras,
            "people": sorted(people.items(), key=lambda x: -x[1])[:12], "first": first[:20],
            "totals": {k: sum(m[k] for m in months.values()) for k in ("photos", "received", "sent")}}
