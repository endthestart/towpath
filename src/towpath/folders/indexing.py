"""Indexing in the background and adding what Recoll found to search, then measuring space."""

import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

from towpath.connections import _now, _update, get
from towpath.folders.choices import PAGE, SID, _jobs, _outcomes, _running, with_files
from towpath.folders.recoll import read_status, _start, _stop
from towpath.stores import open_store, store_path

RATE_WINDOW_SECONDS = 60


def _carried(c: dict) -> dict:
    """From the last progress, only what a new reading pass keeps: the count Search holds."""
    return {"in_search": (c.get("progress") or {}).get("in_search")}


def _progress(status: dict, phase: str, imported: int | None = None, previous: dict | None = None) -> dict:
    """What the Folders page shows. ``indexed`` is what Search holds: the rows added by an import, carried through
    later reading; Recoll's own count of entries (folders and archive members included) isn't shown as files. While
    Recoll reads, ``rate`` is files a minute, measured over at least a minute and smoothed."""
    previous = previous or {}
    files, now = status.get("filesdone"), _now()
    in_search = imported if imported is not None else previous.get("in_search")
    progress = {"phase": phase, "files": files, "docs": status.get("docsdone"), "errors": status.get("fileerrors"),
                "total": status.get("totfiles"), "indexed": in_search, "in_search": in_search, "rate": None,
                "at": now}
    if phase != "reading" or not isinstance(files, int):
        return progress
    since = previous.get("since") if previous.get("phase") == "reading" else None
    if not since or not isinstance(since.get("files"), int) or files < since["files"]:
        return {**progress, "since": {"files": files, "at": now}}
    seconds = (datetime.fromisoformat(now) - datetime.fromisoformat(since["at"])).total_seconds()
    if seconds < RATE_WINDOW_SECONDS:
        return {**progress, "rate": previous.get("rate"), "since": since}
    current = (files - since["files"]) * 60 / seconds
    last = (previous.get("rate") or {}).get("per_minute")
    smoothed = current if last is None else 0.5 * current + 0.5 * last
    return {**progress, "rate": {"per_minute": round(smoothed)}, "since": {"files": files, "at": now}}


def _import(config, c: dict, data_dir: Path, status: dict) -> dict:
    from towpath.discovery import service

    store_dir = config.store_dir
    files_config = with_files(config, data_dir / "index")
    totals = {"seen": 0}

    def progress(seen):
        _update(store_dir, SID, progress=_progress(status, "adding", totals["seen"] + seen))

    def should_stop():
        return (get(store_dir, SID) or {}).get("indexing") != "running"

    reports = []
    for root in c["settings"]["roots"]:
        reports += service.import_catalog(files_config, SID, root["alias"], max_items=100_000_000,
                                          page_size=PAGE, progress=progress, should_stop=should_stop)
        totals["seen"] += reports[-1]["occurrences_seen"]
        if reports[-1]["termination"] == "interrupted":
            break
    return {"reports": reports, "seen": totals["seen"]}


def _kept(config) -> dict[str, list[str]]:
    """The folders the owner keeps off Space's clutter list, read-only from the decisions store."""
    from towpath import space

    if not store_path(config.store_dir, "decisions").exists():
        return {}
    db = open_store(config.store_dir, "decisions", "connect")
    try:
        return space.marks(db, SID)
    finally:
        db.close()


def _unmeasured(config, c: dict) -> bool:
    from towpath import space

    if not store_path(config.store_dir, "files").exists():
        return False
    db = open_store(config.store_dir, "files", "connect")
    try:
        return bool(space.stale(db, SID, [r["alias"] for r in c["settings"].get("roots") or []], _kept(config)))
    finally:
        db.close()


def _measure(config, c: dict) -> list[dict]:
    """Measure space for every root whose newest import, kept folders or measurement version changed (see
    towpath.space), then duplicate candidates across roots."""
    from towpath import space

    roots = [r["alias"] for r in c["settings"].get("roots") or []]
    kept = _kept(config)
    db = open_store(config.store_dir, "files", "connect")
    try:
        done = []
        for root, run in space.stale(db, SID, roots, kept).items():
            started = time.monotonic()
            files = space.rebuild(db, SID, root, run, kept.get(root, ()))["files"]
            done.append({"source_id": SID, "step": "measured", "root": root, "files": files,
                         "seconds": round(time.monotonic() - started, 1)})
        if done:
            started = time.monotonic()
            space.find_duplicates(db, SID)
            done.append({"source_id": SID, "step": "duplicates", "seconds": round(time.monotonic() - started, 1)})
        return done
    finally:
        db.close()


def _in_background(name: str, work) -> None:
    def run():
        try:
            _outcomes[name] = {"result": work()}
        except Exception as exc:  # noqa: BLE001 - reported by the next poll; never log file names from a message
            where = traceback.extract_tb(exc.__traceback__)[-1]
            _outcomes[name] = {"error": type(exc).__name__, "at": f"{Path(where.filename).name}:{where.lineno}"}

    _outcomes.pop(name, None)
    _jobs[name] = threading.Thread(target=run, name=f"towpath-{name}", daemon=True)
    _jobs[name].start()


def _add_and_measure(config, c: dict, data_dir: Path, status: dict) -> dict:
    started = time.monotonic()
    result = _import(config, c, data_dir, status)
    if (get(config.store_dir, SID) or {}).get("indexing") == "paused":
        return {"paused": True}
    _update(config.store_dir, SID, progress=_progress(status, "measuring", result["seen"]))
    _measure(config, c)
    return {"seen": result["seen"], "seconds": round(time.monotonic() - started), "status": status}


def _finished(config, job: str) -> list[dict]:
    """Report a background job that has ended."""
    _jobs.pop(job, None)
    outcome = _outcomes.pop(job, {})
    if job == "measure":
        if "error" in outcome:
            return [{"source_id": SID, "step": "measure-failed", "error": outcome["error"], "at": outcome.get("at")}]
        return outcome.get("result") or []  # per root, with seconds, for the worker's log
    if "error" in outcome:
        failure = {"error": outcome["error"], "at": outcome.get("at")}
        _update(config.store_dir, SID, "indexing-stopped", failure, indexing="idle",
                last_error="Adding files to search stopped with an error. Continue to try again.")
        return [{"source_id": SID, "step": "failed", **failure}]
    result = outcome["result"]
    if result.get("paused"):
        return [{"source_id": SID, "step": "paused-while-adding"}]
    _update(config.store_dir, SID, "indexed", {"files": result["seen"], "seconds": result["seconds"]},
            indexing="idle", progress=_progress(result["status"], "done", result["seen"]))
    return [{"source_id": SID, "step": "indexed", "files": result["seen"]}]


def index_pending(config, data_dir: Path) -> list[dict]:
    """One step of Folders indexing per poll: start Recoll, report its progress, stop it on Pause, and when it
    finishes add the index to search in the background. Never starts anything the owner didn't ask for."""
    c = get(config.store_dir, SID)
    if c is None or c["state"] != "ready":
        return []
    for job in ("add", "measure"):
        if job in _jobs:
            if _jobs[job].is_alive():
                return [{"source_id": SID, "step": "adding" if job == "add" else "measuring"}]
            return _finished(config, job)
    confdir = Path(data_dir) / "index" / "recoll"
    proc = _running.get(SID)
    if c["indexing"] == "paused":
        if proc is not None:
            _stop(proc)
            _running.pop(SID, None)
            return [{"source_id": SID, "step": "paused"}]
        return []
    if c["indexing"] not in ("requested", "running"):
        if c["indexing"] == "idle" and c.get("progress") and _unmeasured(config, c):
            _in_background("measure", lambda: _measure(config, c))
            return [{"source_id": SID, "step": "measuring"}]
        return []
    if proc is None and (c.get("progress") or {}).get("phase") in ("adding", "measuring"):
        # Recoll had finished; adding stopped (a failure, Pause, or a worker restart). Carry on adding rather
        # than walk every folder again. "Index again" after a finished run starts with Recoll as usual.
        if c["indexing"] == "requested":
            _update(config.store_dir, SID, "indexing-resumed", indexing="running", last_error=None)
        status = read_status(confdir)
        _in_background("add", lambda: _add_and_measure(config, c, Path(data_dir), status))
        return [{"source_id": SID, "step": "adding"}]
    if proc is None:
        _running[SID] = _start(c, Path(data_dir))
        _update(config.store_dir, SID, "indexing-started" if c["indexing"] == "requested" else "indexing-resumed",
                indexing="running", last_error=None,
                progress=_progress(read_status(confdir), "reading", previous=_carried(c)))
        return [{"source_id": SID, "step": "started"}]
    if proc.poll() is None:
        _update(config.store_dir, SID, progress=_progress(read_status(confdir), "reading", previous=c["progress"]))
        return [{"source_id": SID, "step": "reading"}]
    _running.pop(SID, None)
    status = read_status(confdir)
    if proc.returncode != 0:
        _update(config.store_dir, SID, "indexing-stopped", {"exit": proc.returncode}, indexing="idle",
                last_error=f"Recoll stopped with an error (exit {proc.returncode}); its log is in the index folder. "
                           "Start again to resume.")
        return [{"source_id": SID, "step": "failed", "exit": proc.returncode}]
    _update(config.store_dir, SID, progress=_progress(status, "adding", 0))
    _in_background("add", lambda: _add_and_measure(config, c, Path(data_dir), status))
    return [{"source_id": SID, "step": "adding"}]
