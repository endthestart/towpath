"""Starting, scheduling and running indexing, which the request worker does in bounded slices."""

import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from towpath.connections.base import (
    ROLE,
    ConnectionProblem,
    _now,
    _require,
    _update,
    all_connections,
    get,
    has_credential,
)
from towpath.connections.effective import merged
from towpath.stores import open_store

SLICE_SECONDS = 300
CHECK_EVERY_SECONDS = 3.0


def start_indexing(store_dir: Path, credentials_dir: Path, source_id: str) -> None:
    found = _require(store_dir, source_id)
    if found["state"] != "ready":
        raise ConnectionProblem("Choose folders first." if found["state"] == "choose-folders"
                                else "Reconnect the account first.")
    if not has_credential(credentials_dir, found):
        raise ConnectionProblem("The sign-in is missing; reconnect first." if found["adapter"] == "gmail"
                                else "The password is missing; replace it first.")
    if found["indexing"] not in ("requested", "running"):
        _update(store_dir, source_id, "indexing-requested", indexing="requested", last_error=None)


# How often a connection checks for changes by itself, once the owner chooses it on its page (off by default).
SCHEDULES = {"off": ("Off: only when you press the button", None), "15m": ("Every 15 minutes", 15 * 60),
             "1h": ("Every hour", 3600), "6h": ("Every 6 hours", 6 * 3600), "1d": ("Every day", 86400),
             "7d": ("Every week", 7 * 86400)}
MAIL_SCHEDULES = ("off", "15m", "1h", "6h", "1d")
FOLDER_SCHEDULES = ("off", "1d", "7d")  # reading every folder again takes long; at most daily


def schedules_for(c: dict) -> tuple[str, ...]:
    return FOLDER_SCHEDULES if c["adapter"] == "recoll" else MAIL_SCHEDULES


def set_schedule(store_dir: Path, source_id: str, schedule: str) -> None:
    found = _require(store_dir, source_id)
    if schedule not in schedules_for(found):
        raise ConnectionProblem("Choose one of the listed schedules.")
    _update(store_dir, source_id, "schedule-set", {"schedule": schedule},
            settings={**found["settings"], "schedule": schedule})


def _last_start(db, source_id: str) -> str | None:
    row = db.execute("SELECT max(at) FROM connection_events WHERE source_id = ? AND event IN "
                     "('indexing-requested', 'indexing-scheduled')", (source_id,)).fetchone()
    return row[0] if row else None


def next_check(store_dir: Path, c: dict, role: str = ROLE) -> datetime | None:
    """When the connection's schedule next starts it, or None when it has no schedule."""
    seconds = SCHEDULES.get((c["settings"] or {}).get("schedule") or "off", (None, None))[1]
    if not seconds:
        return None
    with closing(open_store(store_dir, "connections", role)) as db:
        last = _last_start(db, c["source_id"])
    never = datetime.fromtimestamp(0, timezone.utc)  # never started: due at once
    return datetime.fromisoformat(last) + timedelta(seconds=seconds) if last else never


def start_scheduled(store_dir: Path, credentials_dir: Path, now: datetime | None = None) -> list[dict]:
    """Start each idle, ready connection whose schedule is due. A paused connection stays paused."""
    now = now or datetime.now(timezone.utc)
    started = []
    for c in all_connections(store_dir):
        if c["state"] != "ready" or c["indexing"] != "idle":
            continue
        due = next_check(store_dir, c)
        if due is None or due > now or not has_credential(credentials_dir, c):
            continue
        _update(store_dir, c["source_id"], "indexing-scheduled", indexing="requested", last_error=None)
        started.append({"source_id": c["source_id"], "step": "scheduled"})
    return started


def pause_indexing(store_dir: Path, source_id: str) -> None:
    if _require(store_dir, source_id)["indexing"] in ("requested", "running"):
        _update(store_dir, source_id, "indexing-paused", indexing="paused")


def _plain_stop(termination: str, reason: str | None, adapter: str | None = None) -> str:
    if termination == "auth-stop" and adapter == "gmail":
        return "Google's sign-in for this account has expired. Choose Reconnect Gmail below to sign in again."
    if termination == "auth-stop":
        return "The account rejected Towpath's stored sign-in. Reconnect or replace the password to continue."
    if termination in ("quota-stop", "daily-budget-stop"):
        return "Paused at Gmail's quota limit. Start again later; nothing is lost."
    if termination == "lock-busy":
        return "Another Gmail job was running. Start again in a moment."
    if termination == "server-stop":
        return "The server or network stopped answering. Indexing will resume when you start it again."
    return reason or termination


def _tracker(store_dir: Path, sid: str, deadline: float, clock):
    """Progress that saves counts (never content) for the UI and ends the slice on Pause or at the deadline."""
    from towpath.connect import Capped
    from towpath.progress import Progress

    class Tracker(Progress):
        stop = None

        def __init__(self):
            super().__init__(sid, self.record, every_items=25, every_seconds=5.0)
            self.last_check = clock()

        def item(self):
            super().item()
            now = clock()
            if now - self.last_check >= CHECK_EVERY_SECONDS:
                self.last_check = now
                if (get(store_dir, sid) or {}).get("indexing") != "running":
                    self.stop = "paused"
                    raise Capped("paused by the owner; resume to continue")
            if now >= deadline:
                self.stop = "slice"
                raise Capped("slice finished; indexing continues")

        @staticmethod
        def record(snapshot):
            with closing(open_store(store_dir, "source", "connect")) as db:
                total = db.execute("SELECT COUNT(*) FROM items WHERE source_id = ? AND absent_since_run IS NULL",
                                   (sid,)).fetchone()[0]
            _update(store_dir, sid, progress={"phase": snapshot["phase"], "processed": snapshot["processed"],
                                              "rate": snapshot["rate"], "indexed": total, "at": _now()})

    return Tracker()


def index_pending(config, credentials_dir: Path, slice_seconds: float = SLICE_SECONDS,
                  clock=time.monotonic) -> list[dict]:
    """Run one bounded slice for each connection the owner asked to index. Returns a summary per slice."""
    from towpath import connect
    from towpath.adapters.errors import SyncStop

    store_dir = config.store_dir
    summaries = []
    for c in all_connections(store_dir):
        if c["indexing"] not in ("requested", "running") or c["state"] != "ready" or c["adapter"] in ("recoll", "dav"):
            continue  # folders are indexed by towpath.folders, contacts and calendars by towpath.dav_sync
        sid = c["source_id"]
        _update(store_dir, sid, "indexing-slice" if c["indexing"] == "running" else "indexing-started",
                indexing="running")
        tracker = _tracker(store_dir, sid, clock() + slice_seconds, clock)
        try:
            result = connect.sync(merged(config, credentials_dir), sid, progress=tracker)
        except SyncStop as exc:  # raised before sync could record it, such as a sign-in that has expired
            _update(store_dir, sid, "indexing-stopped", {"termination": exc.code}, indexing="idle",
                    last_error=_plain_stop(exc.code, exc.reason, c["adapter"]))
            summaries.append({"source_id": sid, "termination": exc.code})
            continue
        except Exception as exc:  # noqa: BLE001 - recorded by sync; keep data out of the store
            _update(store_dir, sid, "indexing-error", {"type": type(exc).__name__}, indexing="idle",
                    last_error="Indexing stopped on an unexpected error. Progress is saved; start it again.")
            summaries.append({"source_id": sid, "termination": "error"})
            continue
        termination = result["termination"]
        tracker.record({"phase": "finished", "processed": tracker.processed, "rate": tracker.rate()})
        if termination == "complete":
            _update(store_dir, sid, "indexed", {"total": result["indexed_total"]}, indexing="idle")
        elif not (termination == "capped" and tracker.stop in ("slice", "paused")):
            _update(store_dir, sid, "indexing-stopped", {"termination": termination}, indexing="idle",
                    last_error=_plain_stop(termination, result.get("reason"), c["adapter"]))
        summaries.append({"source_id": sid, "termination": termination, "slice": tracker.stop,
                          "indexed_total": result["indexed_total"]})
    return summaries
