"""Writing what a sync finds: runs, items, and their observations."""

import json
from datetime import datetime, timezone

from towpath.adapters.errors import Interrupted
from towpath.canonical import short_id

ROLE = "connect"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def item_id(source_id: str, native_id: str) -> str:
    return short_id("itm", source_id, native_id)


def _new_run(db, source_id: str, kind: str, cursor_before: str | None) -> str:
    cur = db.execute("INSERT INTO runs (source_id, kind, started_at, cursor_before) VALUES (?, ?, ?, ?)",
                     (source_id, kind, now(), cursor_before))
    run_id = f"run_{cur.lastrowid:04d}"
    db.execute("UPDATE runs SET run_id = ? WHERE seq = ?", (run_id, cur.lastrowid))
    return run_id


def _latest_labels(db, iid: str):
    row = db.execute("SELECT o.labels FROM observations o JOIN runs r ON r.run_id = o.run_id "
                     "WHERE o.item_id = ? ORDER BY r.seq DESC LIMIT 1",
                     (iid,)).fetchone()
    return json.loads(row["labels"]) if row else None


def _observe(db, iid: str, run_id: str, labels: list[str]) -> None:
    if _latest_labels(db, iid) != labels:
        db.execute("INSERT OR REPLACE INTO observations (item_id, run_id, observed_at, labels) VALUES (?, ?, ?, ?)",
                   (iid, run_id, now(), json.dumps(labels)))


def _upsert_item(db, source_id: str, run_id: str, rec: dict) -> bool:
    iid = item_id(source_id, rec["native_id"])
    existing = db.execute("SELECT item_id FROM items WHERE item_id = ?", (iid,)).fetchone()
    fields = (rec["thread_id"], rec["rfc_message_id"], rec["subject"], rec["from_addr"], rec["date_header"],
              rec["date_utc"], rec["internal_date"], rec["size_estimate"], int(rec["structure_truncated"]),
              rec["inline_data_discarded"], json.dumps(rec["problems"]))
    if existing:
        db.execute("""UPDATE items SET thread_id=?, rfc_message_id=?, subject=?, from_addr=?, date_header=?,
                      date_utc=?, internal_date=?, size_estimate=?, structure_truncated=?, inline_data_discarded=?,
                      problems=?, last_seen_run=?, absent_since_run=NULL WHERE item_id=?""",
                   (*fields, run_id, iid))
    else:
        db.execute("""INSERT INTO items (thread_id, rfc_message_id, subject, from_addr, date_header, date_utc,
                      internal_date, size_estimate, structure_truncated, inline_data_discarded, problems,
                      item_id, source_id, native_id, first_seen_run, last_seen_run)
                      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                   (*fields, iid, source_id, rec["native_id"], run_id, run_id))
    for p in rec["parts"]:
        db.execute("""INSERT INTO parts (item_id, part_id, depth, mime_type, charset, filename, disposition, size,
                      attachment_id) VALUES (?,?,?,?,?,?,?,?,?)
                      ON CONFLICT (item_id, part_id) DO UPDATE SET depth=excluded.depth, mime_type=excluded.mime_type,
                      charset=excluded.charset, filename=excluded.filename, disposition=excluded.disposition,
                      size=excluded.size, attachment_id=excluded.attachment_id""",
                   (iid, p["part_id"], p["depth"], p["mime_type"], p["charset"], p["filename"], p["disposition"],
                    p["size"], p["attachment_id"]))
    _observe(db, iid, run_id, rec["labels"])
    return not existing


def _update_matches(db, run_id: str) -> None:
    rows = db.execute("""SELECT rfc_message_id, item_id, source_id FROM items
                         WHERE rfc_message_id IS NOT NULL AND absent_since_run IS NULL""").fetchall()
    by_id: dict[str, list] = {}
    for r in rows:
        by_id.setdefault(r["rfc_message_id"], []).append(r)
    for group in by_id.values():
        for i, left in enumerate(group):
            for right in group[i + 1:]:
                if left["source_id"] == right["source_id"]:
                    continue
                a, b = sorted([left["item_id"], right["item_id"]])
                db.execute("""INSERT OR IGNORE INTO matches (left_item, right_item, strength, basis, run_id)
                              VALUES (?, ?, 'message-id-only', '["rfc_message_id"]', ?)""", (a, b, run_id))


class Capped(Interrupted):
    """The run reached --max-items. Never a completed run."""


def _sanitized(exc: BaseException) -> str:
    """For unexpected errors: the type only, since messages can carry URLs or mail data."""
    return f"unexpected {exc.__class__.__name__}; progress is saved; see `towpath connect status`"


def _full_state(state) -> dict | None:
    if not (state and state["full_sync_history_id"]):
        return None
    return {"history_id": state["full_sync_history_id"], "phase": state["full_sync_phase"] or "scan",
            "page_token": state["full_sync_page_token"]}
