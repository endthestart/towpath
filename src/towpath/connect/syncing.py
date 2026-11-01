"""Indexing a source: a full sync first, incremental after, stopping cleanly and resuming."""

import json

from towpath.adapters import build_connector
from towpath.adapters.errors import Interrupted, SyncStop
from towpath.connect.records import (
    ROLE,
    Capped,
    _full_state,
    item_id,
    _latest_labels,
    _new_run,
    now,
    _observe,
    _sanitized,
    _update_matches,
    _upsert_item,
)
from towpath.progress import Progress
from towpath.stores import open_store


def sync(config, source_id: str, max_items: int | None = None, progress=None, **adapter_options) -> dict:
    """Index one source.

    Every run records how it ended (complete, capped, a named stop, cancelled,
    or error) with its coverage, keeps committed progress, and never advances a
    cursor past unprocessed events. ``max_items`` stops after that many message
    reads; the next run resumes. Clean stops are returned; Ctrl-C and
    unexpected errors are recorded and then re-raised.
    """
    source = config.sources[source_id]
    connector = build_connector(source, config, **adapter_options)
    tracker = progress if progress is not None else Progress(source_id)
    limiter = getattr(connector, "limiter", None)
    if limiter is not None:
        limiter.on_wait = tracker.wait
    batch = config.gmail_pacing.checkpoint_every
    db = open_store(config.store_dir, "source", ROLE)
    pending_error: BaseException | None = None
    try:
        db.execute("INSERT OR REPLACE INTO sources (source_id, kind, adapter, descriptor) VALUES (?, ?, ?, ?)",
                   (source_id, source.kind, source.adapter, json.dumps(connector.describe())))
        state = db.execute("SELECT * FROM sync_state WHERE source_id = ?", (source_id,)).fetchone()
        cursor = state["cursor"] if state else None
        full_state = _full_state(state) if source.kind == "mail-provider" else None
        seen_before = {r["native_id"] for r in db.execute(
            "SELECT native_id FROM full_sync_seen WHERE source_id = ?", (source_id,))} if full_state else set()
        run_kind = "resumed-full" if full_state else ("incremental" if cursor else "full")
        run_id = _new_run(db, source_id, run_kind, cursor)
        db.commit()

        counts = {"seen": 0, "new": 0, "absent": 0, "reads": 0}
        unparseable: list[dict] = []
        outcome = {"kind": run_kind, "complete": False, "cursor": cursor, "termination": "error", "reason": None}
        uncommitted = 0

        def checkpoint(force: bool = False) -> None:
            nonlocal uncommitted
            if force or uncommitted >= batch:
                db.commit()
                uncommitted = 0

        def counted_read() -> None:
            nonlocal uncommitted
            counts["reads"] += 1
            uncommitted += 1
            tracker.item()
            checkpoint()
            if max_items is not None and counts["reads"] >= max_items:
                raise Capped(f"stopped after {max_items} message reads (--max-items); the next run resumes")

        try:
            for event in connector.enumerate(cursor, full_state=full_state, already_seen=seen_before):
                kind = event[0]
                if kind == "cursor_expired":
                    db.execute("UPDATE runs SET kind = 'full-after-expired-cursor' WHERE run_id = ?", (run_id,))
                    outcome["kind"] = "full-after-expired-cursor"
                elif kind == "full_start" and source.kind == "mail-provider":
                    db.execute("""INSERT INTO sync_state (source_id, cursor, full_sync_history_id, full_sync_run,
                                  full_sync_phase, full_sync_page_token) VALUES (?, ?, ?, ?, 'scan', NULL)
                                  ON CONFLICT (source_id) DO UPDATE SET full_sync_history_id =
                                  COALESCE(sync_state.full_sync_history_id, excluded.full_sync_history_id),
                                  full_sync_run = COALESCE(sync_state.full_sync_run, excluded.full_sync_run),
                                  full_sync_phase = 'scan', full_sync_page_token = NULL""",
                               (source_id, cursor, event[1], run_id))
                    db.execute("DELETE FROM full_sync_seen WHERE source_id = ?", (source_id,))
                    db.execute("DELETE FROM full_sync_listed WHERE source_id = ?", (source_id,))
                    checkpoint(force=True)
                    tracker.set_phase("scan")
                elif kind == "page":
                    db.execute("UPDATE sync_state SET full_sync_phase = ?, full_sync_page_token = ? "
                               "WHERE source_id = ?", (event[1], event[2], source_id))
                    tracker.set_phase(event[1])
                elif kind == "phase":
                    db.execute("UPDATE sync_state SET full_sync_phase = ?, full_sync_page_token = NULL "
                               "WHERE source_id = ?", (event[1], source_id))
                    checkpoint(force=True)
                    tracker.set_phase(event[1])
                elif kind == "item":
                    rec = event[1]
                    counts["seen"] += 1
                    counts["new"] += _upsert_item(db, source_id, run_id, rec)
                    if rec["problems"] and any(p.startswith("invalid") or p.startswith("undecodable")
                                               for p in rec["problems"]):
                        unparseable.append({"native_id": rec["native_id"], "problems": rec["problems"]})
                    if outcome["kind"] != "incremental":
                        db.execute("INSERT OR IGNORE INTO full_sync_seen (source_id, native_id) VALUES (?, ?)",
                                   (source_id, rec["native_id"]))
                    counted_read()
                elif kind == "listed":
                    if event[1] == "reconcile":
                        db.executemany("INSERT OR IGNORE INTO full_sync_listed (source_id, native_id) VALUES (?, ?)",
                                       [(source_id, i) for i in event[2]])
                elif kind == "confirm":
                    tracker.set_phase("confirm")
                    candidates = [r["native_id"] for r in db.execute(
                        """SELECT native_id FROM items i WHERE source_id = ? AND absent_since_run IS NULL
                           AND NOT EXISTS (SELECT 1 FROM full_sync_listed l
                                           WHERE l.source_id = i.source_id AND l.native_id = i.native_id)""",
                        (source_id,))]
                    for native_id in candidates:
                        rec = connector.confirm(native_id)
                        if rec is None:
                            counts["absent"] += db.execute(
                                "UPDATE items SET absent_since_run = ? WHERE source_id = ? AND native_id = ? "
                                "AND absent_since_run IS NULL", (run_id, source_id, native_id)).rowcount
                        else:  # still in the mailbox, just not listed (for example in Trash or Spam)
                            _upsert_item(db, source_id, run_id, rec)
                        db.execute("INSERT OR IGNORE INTO full_sync_listed (source_id, native_id) VALUES (?, ?)",
                                   (source_id, native_id))
                        counted_read()
                elif kind == "checkpoint":
                    db.execute("UPDATE sync_state SET cursor = ? WHERE source_id = ?", (event[1], source_id))
                    outcome["cursor"] = event[1]
                    uncommitted += 1
                    checkpoint()
                elif kind == "deleted":
                    counts["absent"] += db.execute(
                        "UPDATE items SET absent_since_run = ? WHERE source_id = ? AND native_id = ? "
                        "AND absent_since_run IS NULL", (run_id, source_id, event[1])).rowcount
                elif kind in {"labels_added", "labels_removed"}:
                    iid = item_id(source_id, event[1])
                    if db.execute("SELECT 1 FROM items WHERE item_id = ?", (iid,)).fetchone():
                        labels = set(_latest_labels(db, iid) or [])
                        changed = set(event[2])
                        labels = labels | changed if kind == "labels_added" else labels - changed
                        _observe(db, iid, run_id, sorted(labels))
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
                    if result["kind"] == "full" and source.kind != "mail-provider":
                        # A destination listing is a single read of a local manifest or folder.
                        seen = {r["native_id"] for r in db.execute(
                            "SELECT native_id FROM full_sync_seen WHERE source_id = ?", (source_id,))}
                        for row in db.execute("SELECT path FROM destination_entries WHERE source_id = ? "
                                              "AND absent_since_run IS NULL", (source_id,)).fetchall():
                            if row["path"] not in seen:
                                db.execute("UPDATE destination_entries SET absent_since_run = ? "
                                           "WHERE source_id = ? AND path = ?", (run_id, source_id, row["path"]))
                                counts["absent"] += 1
                    if result["kind"] == "full":
                        db.execute("DELETE FROM full_sync_seen WHERE source_id = ?", (source_id,))
                        db.execute("DELETE FROM full_sync_listed WHERE source_id = ?", (source_id,))
                    db.execute("""INSERT INTO sync_state (source_id, cursor) VALUES (?, ?)
                                  ON CONFLICT (source_id) DO UPDATE SET cursor = excluded.cursor,
                                  full_sync_history_id = NULL, full_sync_run = NULL, full_sync_phase = NULL,
                                  full_sync_page_token = NULL""", (source_id, result["cursor"]))
                    outcome.update(complete=True, cursor=result["cursor"], termination="complete")
        except Capped as exc:
            outcome.update(termination="capped", reason=str(exc))
        except Interrupted as exc:
            outcome.update(termination="interrupted", reason=str(exc))
        except SyncStop as exc:
            outcome.update(termination=exc.code, reason=exc.reason, exit_code=exc.exit_code)
        except KeyboardInterrupt as exc:
            outcome.update(termination="cancelled", reason="stopped by the operator; the next run resumes")
            pending_error = exc
        except Exception as exc:  # noqa: BLE001 - recorded, then re-raised below
            outcome.update(termination="error", reason=_sanitized(exc))
            pending_error = exc

        db.commit()
        if source.kind == "mail-provider":
            _update_matches(db, run_id)
        total_table = "items" if source.kind == "mail-provider" else "destination_entries"
        total = db.execute(f"SELECT COUNT(*) FROM {total_table} WHERE source_id = ? AND absent_since_run IS NULL",
                           (source_id,)).fetchone()[0]
        db.execute("""UPDATE runs SET finished_at = ?, complete = ?, cursor_after = ?, termination = ?, reason = ?,
                      items_processed = ? WHERE run_id = ?""",
                   (now(), int(outcome["complete"]), outcome["cursor"], outcome["termination"], outcome["reason"],
                    counts["reads"], run_id))
        db.execute("""INSERT INTO coverage (run_id, source_id, complete, items_seen, items_new, items_absent,
                      items_indexed_total, unparseable, termination, reason) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                   (run_id, source_id, int(outcome["complete"]), counts["seen"], counts["new"], counts["absent"],
                    total, json.dumps(unparseable), outcome["termination"], outcome["reason"]))
        db.commit()
        tracker.finish(outcome["termination"], outcome["reason"])
    finally:
        db.close()
        if hasattr(connector, "close"):
            connector.close()
    if pending_error is not None:
        raise pending_error
    counts.pop("reads")
    return {"run_id": run_id, "source_id": source_id, **outcome, **counts, "indexed_total": total,
            "unparseable": unparseable}
