"""The Overview page's "Today" cards: what needs the owner across mail, the life story and photos."""

from towpath import connections, dav_sync, life, life_groups, life_today, photos
from towpath.mail import FEATURE, senders, settings as mail_settings, threads
from towpath.unified import grants


def cards(store_dir) -> dict:
    allowed = grants.granted(store_dir)
    accounts = []
    for c in connections.all_connections(store_dir, role="web"):
        if FEATURE not in allowed.get(c["source_id"], set()):
            continue
        counts = threads.counts(store_dir, c["source_id"])
        inbox = senders.inbox(store_dir, c["source_id"])
        accounts.append({"source_id": c["source_id"], "name": c["display_name"],
                         "inbox": inbox["total"], "inbox_unread": inbox["unread"],
                         "to_reply": counts.get("needs_owner_reply", 0), "awaiting": counts.get("awaiting_others", 0),
                         "dry_run": mail_settings.dry_run(store_dir, c["source_id"], role="web")})
    claims = life.claims(store_dir, limit=10**6)
    groups = life_groups.groups(store_dir)
    today = life_today.on_this_day(store_dir)
    return {"accounts": accounts,
            "events_to_review": sum(c.state == "proposed" and c.significance != "routine" for c in claims),
            "memories_to_review": sum(g["state"] == "proposed" for g in groups),
            "on_this_day": len(today["years"]), "photos": photos.progress(store_dir).get("read", 0),
            "birthdays": dav_sync.birthdays_ahead(store_dir, role="web"),
            "week": dav_sync.week_ahead(store_dir, role="web")}
