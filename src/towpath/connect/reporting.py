"""Coverage and status for a source, without message data, IDs or tokens."""

import json

from towpath.connect.records import ROLE
from towpath.stores import open_store


def coverage(config, source_id: str) -> list[dict]:
    db = open_store(config.store_dir, "source", ROLE)
    rows = [dict(r) for r in db.execute(
        """SELECT c.*, r.kind FROM coverage c JOIN runs r USING (run_id) WHERE c.source_id = ?
           ORDER BY c.run_id""", (source_id,))]
    db.close()
    for r in rows:
        r["unparseable"] = json.loads(r["unparseable"])
    return rows


def status(config, source_id: str, runs: int = 5) -> dict:
    """Recent runs, resume state, and budget use. No message data, IDs, cursors, or page tokens."""
    from towpath import quota as quota_mod
    from towpath.quota import BudgetLock, budget_usage

    source = config.sources[source_id]
    db = open_store(config.store_dir, "source", ROLE)
    try:
        recent = [dict(r) for r in db.execute(
            """SELECT r.run_id, r.kind, r.started_at, r.finished_at, r.complete, r.termination, r.reason,
                      r.items_processed, c.items_new, c.items_absent, c.items_indexed_total
               FROM runs r LEFT JOIN coverage c USING (run_id) WHERE r.source_id = ? ORDER BY r.seq DESC LIMIT ?""",
            (source_id, runs))]
        state = db.execute("SELECT * FROM sync_state WHERE source_id = ?", (source_id,)).fetchone()
        indexed = db.execute("SELECT COUNT(*) FROM items WHERE source_id = ? AND absent_since_run IS NULL",
                             (source_id,)).fetchone()[0]
        seen = db.execute("SELECT COUNT(*) FROM full_sync_seen WHERE source_id = ?", (source_id,)).fetchone()[0]
    finally:
        db.close()
    report = {"source": source_id, "indexed": indexed, "runs": recent,
              "full_sync_in_progress": bool(state and state["full_sync_history_id"]),
              "full_sync_phase": state["full_sync_phase"] if state else None,
              "full_sync_read_so_far": seen,
              "resume_page_saved": bool(state and state["full_sync_page_token"]),
              "incremental_cursor_saved": bool(state and state["cursor"])}
    if source.adapter == "gmail":
        pacing = config.gmail_pacing
        quota = open_store(config.store_dir, "quota", ROLE)
        try:
            report["budget"] = budget_usage(quota, pacing, source_id, quota_mod.CLOCK())
        finally:
            quota.close()
        report["budget"]["lock_held_by_another_command"] = BudgetLock.is_held_elsewhere(config.store_dir,
                                                                                         pacing.budget_id)
        report["pacing"] = {"min_interval_seconds": pacing.min_interval_seconds,
                            "units_per_minute": pacing.units_per_minute, "daily_units": pacing.daily_units}
    return report
