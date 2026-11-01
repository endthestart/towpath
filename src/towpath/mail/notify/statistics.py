"""The account's statistics."""

import json
import statistics
from contextlib import closing
from datetime import datetime, timedelta

from towpath.mail.notify.common import ROLE, now
from towpath.stores import open_store, store_path


def measure(store_dir, source_id: str, role: str = ROLE) -> dict:
    """Mail statistics from the mail store, stored for the Statistics page."""
    from towpath.mail import senders, threads

    cats = senders.categories(store_dir, source_id, role)

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
        recent = (now() - timedelta(days=90)).isoformat()
        by_category: dict[str, list[int]] = {}
        top_senders: dict[str, int] = {}
        for m in mail.execute(f"SELECT subject, from_addr, list_id, flags FROM messages WHERE source_id = ? AND "
                              f"date_utc >= ? AND mailbox NOT IN ({marks})", (source_id, recent, *sent)):
            c = senders.message_category(dict(m), cats.get(m["from_addr"]))
            got = by_category.setdefault(c, [0, 0])
            got[0] += 1
            got[1] += "\\Seen" not in m["flags"]
            if m["from_addr"]:
                top_senders[m["from_addr"]] = top_senders.get(m["from_addr"], 0) + 1
        delays = []
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
            "by_category_90": sorted(([senders.CATEGORIES.get(c, c), n, u] for c, (n, u) in by_category.items()),
                                     key=lambda x: -x[1]),
            "top_senders_90": sorted(top_senders.items(), key=lambda x: -x[1])[:15],
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
