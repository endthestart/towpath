"""On this day, and each year at a glance: the owner's records from the same date in earlier years, and a year's
photos, mail and the people written to most.

Read-only views over stores other jobs keep: life-story events, photo metadata and mail management's mail store.
"""

import json
from contextlib import closing
from datetime import date

from towpath import life
from towpath.stores import open_store, store_path


def on_this_day(store_dir, day: date | None = None, role: str = "web") -> dict:
    """Events, photos and the owner's own mail from this month and day in earlier years, newest year first."""
    day = day or date.today()
    md = day.strftime("%m-%d")
    events = [c for c in life.claims(store_dir, limit=10**6, role=role)
              if c.state != "rejected" and (c.when.earliest or "")[5:10] == md and
              (c.when.earliest or "")[:4] < str(day.year) and c.significance != "routine"]
    photos = []
    if store_path(store_dir, "files").exists():
        with closing(open_store(store_dir, "files", role)) as db:
            photos = [dict(r) for r in db.execute(
                "SELECT p.taken, p.model, p.occurrence_id, o.provider_id, o.root_alias, o.rel_path FROM photo_meta p "
                "JOIN occurrences o "
                "USING (occurrence_id) WHERE substr(p.taken, 6, 5) = ? AND substr(p.taken, 1, 4) < ? AND "
                "o.missing_since_run IS NULL ORDER BY p.taken DESC LIMIT 60", (md, str(day.year)))]
    sent = []
    if store_path(store_dir, "mail").exists():
        from towpath.mail import threads

        with closing(open_store(store_dir, "mail", role)) as db:
            for sid in [r[0] for r in db.execute("SELECT DISTINCT source_id FROM messages")]:
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


def year_context(store_dir, role: str = "web") -> dict[str, dict]:
    """Per year: photos taken, mail received and sent, and the people written to most."""
    out: dict[str, dict] = {}
    if store_path(store_dir, "files").exists():
        with closing(open_store(store_dir, "files", role)) as db:
            for year, n in db.execute("SELECT substr(taken, 1, 4), count(*) FROM photo_meta WHERE taken IS NOT NULL "
                                      "GROUP BY 1"):
                out.setdefault(year, {})["photos"] = n
    if store_path(store_dir, "mail").exists():
        from towpath.mail import threads

        with closing(open_store(store_dir, "mail", role)) as db:
            for sid in [r[0] for r in db.execute("SELECT DISTINCT source_id FROM messages")]:
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
