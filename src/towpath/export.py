"""The owner's export package: what Towpath has learned and what the owner decided, as one zip of plain JSON and
Markdown, readable without Towpath.

It holds the life story (events with their sources, reviews, memories, the owner's recollections and profile, the
story as Markdown), people, and per mail account the owner's rules, sender categories (and which were the owner's),
conversation marks, purchases, settings and filed documents, plus the record of every decision. It holds no mail
text, no file or photo contents and no credentials: those stay where they are. Only sources the owner allows for the
life story contribute life events, as everywhere else.
"""

import io
import json
import sqlite3
import zipfile
from contextlib import closing
from datetime import datetime, timezone

from towpath.stores import open_store, store_path

ROLE = "web"

README = """# Towpath export, {when}

What Towpath learned from your sources and what you decided, readable without Towpath. No mail text, file or photo
contents, or passwords are included; each record names its source instead (a message, file or photo reference).

- `life/events.json`: life events with their dates, sources and your review (kept, rejected, edited).
- `life/memories.json`: memories (people, chapters, trips, occasions) and which events they hold.
- `life/profile.json`: your name and birthday, as you gave them.
- `life/story.md`: your story, as on the Story page.
- `people.json`: everyone you correspond with, with mail counts and dates.
- `mail/<account>/`: your rules, sender categories, conversation marks, purchases, settings and filed documents.
- `decisions.json`: every decision recorded, with who made it and when.
- `restore/`: your decisions in the form Towpath reads back (Life → Export → *Restore*), to bring them into a new
  Towpath. Permissions, model choices and anything that changes mail are never restored from a file.
"""


def _json(data) -> bytes:
    return json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")


def _rows(store_dir, store: str, sql: str, args=()) -> list[dict]:
    if not store_path(store_dir, store).exists():
        return []
    with closing(open_store(store_dir, store, ROLE)) as db:
        try:
            return [dict(r) for r in db.execute(sql, args)]
        except sqlite3.OperationalError:
            return []


def build(store_dir) -> tuple[str, bytes]:
    """(file name, zip bytes)."""
    from towpath import life, life_groups, life_story, people, restore
    from towpath.mail import FEATURE, purchases, rules, senders, settings as mail_settings, threads
    from towpath.unified import grants

    when = datetime.now(timezone.utc)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("README.md", README.format(when=when.strftime("%Y-%m-%d %H:%M UTC")))
        z.writestr("life/events.json", _json([{**c.to_dict(), "state": c.state}
                                                  for c in life.claims(store_dir, limit=10**6, role=ROLE)]))
        z.writestr("life/memories.json", _json(life_groups.groups(store_dir, ROLE)))
        z.writestr("life/profile.json", _json(life_groups.profile(store_dir, ROLE)))
        z.writestr("life/story.md", life_story.markdown(life_story.story(store_dir)))
        z.writestr("people.json", _json(people.everyone(store_dir, limit=10**6, role=ROLE)))
        for sid in sorted(s for s, f in grants.granted(store_dir).items() if FEATURE in f):
            base = f"mail/{sid}/"
            z.writestr(base + "rules.json", _json(rules.all_rules(store_dir, sid, ROLE)))
            z.writestr(base + "senders.json", _json([
                {k: s[k] for k in ("address", "name", "category", "messages", "first_date", "last_date")} |
                {"chosen_by_you": s["address"] in senders.marks(store_dir, sid, ROLE)}
                for s in senders.listing(store_dir, sid, limit=10**6, role=ROLE)]))
            z.writestr(base + "conversation_marks.json", _json(threads.marks(store_dir, sid, ROLE)))
            z.writestr(base + "purchases.json", _json(purchases.listing(store_dir, sid, limit=10**6, role=ROLE)))
            z.writestr(base + "settings.json", _json(_rows(
                store_dir, "decisions", "SELECT key, value, author, at FROM settings WHERE target_id = ?",
                (mail_settings.target(sid),))))
            z.writestr(base + "filed_documents.json", _json(_rows(
                store_dir, "mail", "SELECT filename, path, size, sha256, status, at FROM filed WHERE source_id = ? AND "
                "status IN ('filed', 'same')", (sid,))))
        z.writestr("decisions.json", _json(_rows(store_dir, "decisions", "SELECT * FROM decision_log ORDER BY rowid")))
        z.writestr("restore/version.json", _json({"version": restore.VERSION}))
        for name, rows in restore.package(store_dir).items():
            z.writestr(f"restore/{name}.json", _json(rows))
    return f"towpath-export-{when:%Y-%m-%d}.zip", buf.getvalue()
