"""The ``towpath-connect`` role: the only code that reads sources.

It writes the source index and reads the work queue. It never calls a model
and has no way to change a source.
"""

import base64
import hashlib
import json
from datetime import datetime, timezone

from towpath.adapters import build_connector
from towpath.adapters.errors import Interrupted, NotFound
from towpath.canonical import short_id
from towpath.stores import open_store

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
    row = db.execute("SELECT labels FROM observations WHERE item_id = ? ORDER BY run_id DESC LIMIT 1",
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


def sync(config, source_id: str, max_items: int | None = None, **adapter_options) -> dict:
    """Index one source. ``max_items`` stops after that many messages; the next run resumes."""
    source = config.sources[source_id]
    connector = build_connector(source, **adapter_options)
    db = open_store(config.store_dir, "source", ROLE)
    db.execute("INSERT OR REPLACE INTO sources (source_id, kind, adapter, descriptor) VALUES (?, ?, ?, ?)",
               (source_id, source.kind, source.adapter, json.dumps(connector.describe())))
    state = db.execute("SELECT * FROM sync_state WHERE source_id = ?", (source_id,)).fetchone()
    cursor = state["cursor"] if state else None
    resuming = bool(state and state["full_sync_history_id"])
    seen_before = {r["native_id"] for r in db.execute(
        "SELECT native_id FROM full_sync_seen WHERE source_id = ?", (source_id,))} if resuming else set()
    run_kind = "resumed-full" if resuming else ("incremental" if cursor else "full")
    run_id = _new_run(db, source_id, run_kind, cursor)
    db.commit()

    counts = {"seen": 0, "new": 0, "absent": 0}
    unparseable: list[dict] = []
    outcome = {"kind": run_kind, "complete": False, "cursor": cursor}
    events = connector.enumerate(cursor, full_sync_history_id=state["full_sync_history_id"] if resuming else None,
                                 already_seen=seen_before)
    try:
        for event in events:
            kind = event[0]
            if kind == "cursor_expired":
                db.execute("UPDATE runs SET kind = 'full-after-expired-cursor' WHERE run_id = ?", (run_id,))
                outcome["kind"] = "full-after-expired-cursor"
            elif kind == "full_start" and source.kind == "mail-provider":
                db.execute("""INSERT INTO sync_state (source_id, cursor, full_sync_history_id, full_sync_run)
                              VALUES (?, ?, ?, ?) ON CONFLICT (source_id) DO UPDATE SET
                              full_sync_history_id = excluded.full_sync_history_id,
                              full_sync_run = COALESCE(sync_state.full_sync_run, excluded.full_sync_run)""",
                           (source_id, cursor, event[1], run_id))
                db.commit()
            elif kind == "item":
                rec = event[1]
                counts["seen"] += 1
                counts["new"] += _upsert_item(db, source_id, run_id, rec)
                if rec["problems"] and any(p.startswith("invalid") or p.startswith("undecodable")
                                           for p in rec["problems"]):
                    unparseable.append({"native_id": rec["native_id"], "problems": rec["problems"]})
                db.execute("INSERT OR IGNORE INTO full_sync_seen (source_id, native_id) VALUES (?, ?)",
                           (source_id, rec["native_id"]))
                db.commit()  # progress survives an interruption
                if max_items is not None and counts["seen"] >= max_items:
                    raise Interrupted(f"stopped after {max_items} messages (--max-items); the next run resumes")
            elif kind == "deleted":
                n = db.execute("UPDATE items SET absent_since_run = ? WHERE source_id = ? AND native_id = ? "
                               "AND absent_since_run IS NULL", (run_id, source_id, event[1])).rowcount
                counts["absent"] += n
            elif kind == "labels":
                iid = item_id(source_id, event[1])
                if db.execute("SELECT 1 FROM items WHERE item_id = ?", (iid,)).fetchone():
                    _observe(db, iid, run_id, event[2])
            elif kind == "destination_entry":
                e = event[1]
                counts["seen"] += 1
                existing = db.execute("SELECT 1 FROM destination_entries WHERE source_id=? AND path=?",
                                      (source_id, e["path"])).fetchone()
                counts["new"] += not existing
                db.execute("""INSERT INTO destination_entries (source_id, path, algorithm, checksum, size,
                              first_seen_run) VALUES (?,?,?,?,?,?) ON CONFLICT (source_id, path) DO UPDATE SET
                              algorithm=excluded.algorithm, checksum=excluded.checksum, size=excluded.size,
                              absent_since_run=NULL""",
                           (source_id, e["path"], e["algorithm"], e["checksum"], e["size"], run_id))
                db.execute("INSERT OR IGNORE INTO full_sync_seen (source_id, native_id) VALUES (?, ?)",
                           (source_id, e["path"]))
            elif kind == "done":
                result = event[1]
                if result["kind"] == "full":
                    seen = {r["native_id"] for r in db.execute(
                        "SELECT native_id FROM full_sync_seen WHERE source_id = ?", (source_id,))}
                    table, key = (("items", "native_id") if source.kind == "mail-provider"
                                  else ("destination_entries", "path"))
                    present = db.execute(f"SELECT {key} FROM {table} WHERE source_id = ? AND absent_since_run IS NULL",
                                         (source_id,)).fetchall()
                    for row in present:
                        if row[key] not in seen:
                            db.execute(f"UPDATE {table} SET absent_since_run = ? WHERE source_id = ? AND {key} = ?",
                                       (run_id, source_id, row[key]))
                            counts["absent"] += 1
                    db.execute("DELETE FROM full_sync_seen WHERE source_id = ?", (source_id,))
                db.execute("""INSERT INTO sync_state (source_id, cursor) VALUES (?, ?)
                              ON CONFLICT (source_id) DO UPDATE SET cursor = excluded.cursor,
                              full_sync_history_id = NULL, full_sync_run = NULL""", (source_id, result["cursor"]))
                outcome.update(complete=True, cursor=result["cursor"])
    except Interrupted as exc:
        outcome["interrupted"] = str(exc)

    if source.kind == "mail-provider":
        _update_matches(db, run_id)
    total_table = "items" if source.kind == "mail-provider" else "destination_entries"
    total = db.execute(f"SELECT COUNT(*) FROM {total_table} WHERE source_id = ? AND absent_since_run IS NULL",
                       (source_id,)).fetchone()[0]
    db.execute("UPDATE runs SET finished_at = ?, complete = ?, cursor_after = ? WHERE run_id = ?",
               (now(), int(outcome["complete"]), outcome["cursor"], run_id))
    db.execute("""INSERT INTO coverage (run_id, source_id, complete, items_seen, items_new, items_absent,
                  items_indexed_total, unparseable) VALUES (?,?,?,?,?,?,?,?)""",
               (run_id, source_id, int(outcome["complete"]), counts["seen"], counts["new"], counts["absent"], total,
                json.dumps(unparseable)))
    db.commit()
    db.close()
    return {"run_id": run_id, "source_id": source_id, **outcome, **counts, "indexed_total": total,
            "unparseable": unparseable}


def _hashes(data: bytes) -> dict:
    return {"sha256": hashlib.sha256(data).hexdigest(),
            "sha1_b64": base64.b64encode(hashlib.sha1(data).digest()).decode("ascii"),
            "md5": hashlib.md5(data).hexdigest()}


def fetch_requests(config) -> dict:
    """Fulfill content requests from the work queue, one part at a time."""
    db = open_store(config.store_dir, "source", ROLE)
    queue = open_store(config.store_dir, "queue", ROLE)
    done = {r["request_id"] for r in db.execute("SELECT request_id FROM fetches")}
    connectors: dict[str, object] = {}
    run_ids: dict[str, str] = {}
    result = {"fetched": 0, "failed": 0, "skipped": 0}
    for req in queue.execute("SELECT * FROM content_requests ORDER BY seq"):
        request_id = f"req_{req['seq']:06d}"
        if request_id in done:
            continue
        item = db.execute("SELECT source_id, native_id, absent_since_run FROM items WHERE item_id = ?",
                          (req["item_id"],)).fetchone()
        if item is None or item["absent_since_run"]:
            db.execute("INSERT INTO fetches VALUES (?,?,?,?,?,?)",
                       (request_id, req["item_id"], req["part_id"], "skipped", "item absent", now()))
            result["skipped"] += 1
            continue
        source_id = item["source_id"]
        if source_id not in connectors:
            connectors[source_id] = build_connector(config.sources[source_id])
            run_ids[source_id] = _new_run(db, source_id, "fetch", None)
        try:
            data = connectors[source_id].fetch(item["native_id"], req["part_id"])
        except NotFound as exc:
            db.execute("INSERT INTO fetches VALUES (?,?,?,?,?,?)",
                       (request_id, req["item_id"], req["part_id"], "failed", str(exc), now()))
            result["failed"] += 1
            continue
        h = _hashes(data)
        db.execute("INSERT OR IGNORE INTO cache (sha256, bytes, cached_at) VALUES (?, ?, ?)",
                   (h["sha256"], data, now()))
        db.execute("UPDATE parts SET sha256=?, sha1_b64=?, md5=?, fetched_run=? WHERE item_id=? AND part_id=?",
                   (h["sha256"], h["sha1_b64"], h["md5"], run_ids[source_id], req["item_id"], req["part_id"]))
        db.execute("INSERT INTO fetches VALUES (?,?,?,?,?,?)",
                   (request_id, req["item_id"], req["part_id"], "fetched", h["sha256"], now()))
        result["fetched"] += 1
        db.commit()
    result.update(_presence_lookups(config, db, queue, connectors, run_ids))
    for run_id in run_ids.values():
        db.execute("UPDATE runs SET finished_at = ?, complete = 1 WHERE run_id = ?", (now(), run_id))
    db.commit()
    db.close()
    queue.close()
    return result


def _presence_lookups(config, db, queue, connectors, run_ids) -> dict:
    """Ask lookup destinations whether they already hold requested checksums.

    Results are observations at a run, not guarantees: a delivery must check again.
    """
    answered = {(r["source_id"], r["algorithm"], r["checksum"]) for r in db.execute(
        "SELECT source_id, algorithm, checksum FROM destination_lookups")}
    pending: dict[tuple[str, str], list[str]] = {}
    for req in queue.execute("SELECT * FROM presence_requests ORDER BY seq"):
        key = (req["destination_id"], req["algorithm"], req["checksum"])
        if key not in answered:
            pending.setdefault((req["destination_id"], req["algorithm"]), []).append(req["checksum"])
    counts = {"presence_checked": 0}
    for (dest_id, algorithm), checksums in pending.items():
        if dest_id not in connectors:
            connectors[dest_id] = build_connector(config.sources[dest_id])
            run_ids[dest_id] = _new_run(db, dest_id, "lookup", None)
        for checksum, (present, remote_id) in connectors[dest_id].lookup(algorithm, checksums).items():
            db.execute("""INSERT OR REPLACE INTO destination_lookups (source_id, algorithm, checksum, present,
                          remote_id, checked_run, checked_at) VALUES (?,?,?,?,?,?,?)""",
                       (dest_id, algorithm, checksum, int(present), remote_id, run_ids[dest_id], now()))
            counts["presence_checked"] += 1
        db.commit()
    return counts


def coverage(config, source_id: str) -> list[dict]:
    db = open_store(config.store_dir, "source", ROLE)
    rows = [dict(r) for r in db.execute(
        """SELECT c.*, r.kind FROM coverage c JOIN runs r USING (run_id) WHERE c.source_id = ?
           ORDER BY c.run_id""", (source_id,))]
    db.close()
    for r in rows:
        r["unparseable"] = json.loads(r["unparseable"])
    return rows
