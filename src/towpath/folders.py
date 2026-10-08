"""Folders on the NAS, indexed with Recoll from the Connections page (towpath-connect only).

The owner picks folders under the read-only library mount (``/library`` by default). Towpath writes the
Recoll configuration, runs ``recollindex`` in the background at low priority, shows its progress, and then
lists the index page by page into the files store so names, types, sizes and dates are searchable at once.
File contents are searched through Recoll by the request worker. Pause stops ``recollindex``; it resumes
from what is already indexed. Nothing here writes to the folders being indexed: they are mounted read-only,
and Recoll only reads them. See docs/specs/connections-in-the-ui.md.
"""

import json
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from datetime import datetime
from contextlib import closing
from pathlib import Path

from towpath import connections
from towpath.connections import ConnectionProblem, _now, _update, get
from towpath.stores import open_store, store_path

SID = "folders"
PAGE = 20000  # rows per Recoll call while adding to search; each call starts the bridge afresh
LIBRARY_ENV = "TOWPATH_LIBRARY"
# Files Recoll should index by name only: photos, video, audio, disk images and binaries have no useful text,
# and reading them all would only add load on the pool. Types Recoll doesn't recognise are listed by name too.
NAME_ONLY = (".jpg .jpeg .png .gif .heic .heif .webp .bmp .tif .tiff .nef .cr2 .cr3 .arw .dng .raf .orf .rw2 .psd "
             ".mp4 .mov .avi .mkv .m4v .wmv .mpg .mpeg .3gp .mts .m2ts .mp3 .flac .wav .m4a .aac .ogg .opus .aiff "
             ".iso .img .dmg .vmdk .qcow2 .vdi .vhd .vhdx .bin .exe .dll .so .dylib .o .a .class .jar .pyc .db "
             ".sqlite .sparsebundle .band")
# Everything is listed, including caches, recycle bins and system folders, because finding what can be deleted
# is part of the point. Only snapshot views are skipped: they repeat every file under another path.
SKIPPED_NAMES = ".zfs .snapshot"
# Indexing settings the owner can change on the Folders page. The defaults suit a NAS of spinning disks that
# also serves other apps: gentle on random reads, every file listed, contents read only for documents.
DEFAULTS = {
    "threads": 2,  # files Recoll reads at once (Recoll's own default is 4)
    "name_only": NAME_ONLY,
    "skip": SKIPPED_NAMES,
    "sniff": False,  # open files with unrecognised names to guess their type
    "text_limit_mb": 20,
    "compressed_limit_mb": 100,
    # Text gathered in memory before Recoll writes it to its index. Each write merges into the whole index, so on
    # a large index on spinning disks small batches spend most of the time rewriting it (Recoll's default is 50).
    "batch_mb": 512,
}
_TOKEN = re.compile(r'[^\s"\\=]{1,100}')
_running: dict[str, subprocess.Popen] = {}
# Adding to search and measuring space run beside the worker's loop, so mail checks and searches never wait the
# hour a large import takes. Each job's outcome waits here until the next poll reports it.
_jobs: dict[str, threading.Thread] = {}
_outcomes: dict[str, dict] = {}


def library() -> Path:
    return Path(os.environ.get(LIBRARY_ENV, "/library"))


def _inside(base: Path, path: Path) -> bool:
    base, path = os.path.realpath(base), os.path.realpath(path)
    return path == base or path.startswith(base.rstrip("/") + "/")


def tree(base: Path | None = None, depth: int = 2) -> list[dict]:
    """Folders under the library, ``depth`` levels deep, for the picker. Unreadable folders are shown, not
    hidden, so a permission problem is visible."""
    base = Path(base or library())

    def level(path: Path, d: int) -> list[dict]:
        try:
            entries = sorted((e for e in os.scandir(path) if e.is_dir(follow_symlinks=False)
                              and not e.name.startswith((".", "@", "#"))), key=lambda e: e.name.lower())
        except OSError:
            return []
        out = []
        for e in entries:
            readable = os.access(e.path, os.R_OK | os.X_OK)
            out.append({"name": e.name, "path": e.path, "rel": os.path.relpath(e.path, base),
                        "readable": readable, "children": level(Path(e.path), d - 1) if readable and d > 1 else []})
        return out

    return level(base, depth) if base.is_dir() else []


def _alias(rel: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", rel.lower()).strip("-")[:28] or "folder"
    if not base[0].isalnum():
        base = "f" + base
    alias, n = base, 1
    while alias in taken:
        n += 1
        alias = f"{base[:28 - len(str(n))]}-{n}"
    return alias


def options(c: dict | None) -> dict:
    """The indexing settings in force: the defaults, overridden by what the owner saved."""
    saved = ((c or {}).get("settings") or {}).get("indexing") or {}
    return {**DEFAULTS, **{k: v for k, v in saved.items() if k in DEFAULTS}}


def _number(raw, low: int, high: int, what: str) -> int:
    try:
        value = int(str(raw).strip())
    except ValueError:
        raise ConnectionProblem(f"{what} must be a whole number.") from None
    if not low <= value <= high:
        raise ConnectionProblem(f"{what} must be between {low} and {high}.")
    return value


def _names(raw: str, what: str, suffixes: bool = False) -> str:
    tokens = str(raw).split()
    if len(tokens) > 500:
        raise ConnectionProblem(f"{what}: at most 500 entries.")
    for token in tokens:
        if not _TOKEN.fullmatch(token) or (suffixes and not (token.startswith(".") and len(token) > 1)):
            hint = " Each starts with a dot, like .iso." if suffixes else " Names can't contain spaces or quotes."
            raise ConnectionProblem(f"{what}: {token[:40]!r} isn't valid.{hint}")
    return " ".join(t.lower() for t in tokens) if suffixes else " ".join(tokens)


def set_options(config, form: dict) -> dict:
    """Validate and save indexing settings from the Folders page; they apply when indexing next starts."""
    c = get(config.store_dir, SID)
    if c is None:
        raise ConnectionProblem("Choose folders first.")
    values = {
        "threads": _number(form.get("threads", ""), 1, 16, "Files read at once"),
        "name_only": _names(form.get("name_only", ""), "Index by name only", suffixes=True),
        "skip": _names(form.get("skip", ""), "Skip these names"),
        "sniff": bool(form.get("sniff")),
        "text_limit_mb": _number(form.get("text_limit_mb", ""), 1, 2000, "Largest text file"),
        "compressed_limit_mb": _number(form.get("compressed_limit_mb", ""), 0, 10000, "Largest compressed file"),
        "batch_mb": _number(form.get("batch_mb", ""), 16, 8192, "Indexing batch"),
    }
    changed = {k: v for k, v in values.items() if v != options(c)[k]}
    _update(config.store_dir, SID, "settings-changed", {"changed": sorted(changed)},
            settings={**c["settings"], "indexing": {k: v for k, v in values.items() if v != DEFAULTS[k]}})
    return values


def choose(config, picks: list[str], base: Path | None = None) -> str:
    """Record the folders to index (replacing the previous choice) and grant them for search. A folder
    inside another chosen folder is dropped, since the parent already covers it."""
    from towpath.discovery import policy

    base = Path(base or library())
    chosen = []
    for raw in sorted(set(picks), key=len):
        path = Path(raw)
        if not _inside(base, path) or os.path.realpath(path) == os.path.realpath(base) or not path.is_dir():
            raise ConnectionProblem(f"{raw} is not a folder in the library.")
        if not os.access(path, os.R_OK | os.X_OK):
            raise ConnectionProblem(f"Towpath can't read {os.path.relpath(path, base)}. Give it read access first.")
        if not any(_inside(c, path) for c in chosen):
            chosen.append(path)
    if not chosen:
        raise ConnectionProblem("Choose at least one folder.")
    existing = get(config.store_dir, SID)
    previous = {r["path"]: r["alias"] for r in (existing["settings"]["roots"] if existing else [])}
    roots, taken = [], set()
    for path in chosen:
        alias = previous.get(str(path)) or _alias(os.path.relpath(path, base), taken | set(previous.values()))
        taken.add(alias)
        roots.append({"alias": alias, "path": str(path), "rel": os.path.relpath(path, base)})
    settings = {"roots": roots, "username": None,
                **({"indexing": existing["settings"]["indexing"]} if existing and existing["settings"].get("indexing")
                   else {})}
    if existing is None:
        now = _now()
        with closing(open_store(config.store_dir, "connections", connections.ROLE)) as db:
            db.execute("""INSERT INTO connections (source_id, provider, adapter, display_name, settings, state,
                          indexing, created_at, updated_at) VALUES (?, 'folders', 'recoll', 'Folders', ?, 'ready',
                          'idle', ?, ?)""", (SID, json.dumps(settings), now, now))
            db.execute("INSERT INTO connection_events (source_id, at, event) VALUES (?, ?, 'added')", (SID, now))
            db.commit()
    else:
        _update(config.store_dir, SID, "folders-chosen", {"folders": len(roots)}, settings=settings, state="ready")
    granted_config = with_files(config, Path(config.store_dir).parent / "index")
    for root in roots:
        policy.grant(granted_config, root["alias"], "search", author="owner (Connections page)")
    return SID


def files_table(c: dict, index_dir: Path) -> dict:
    """The ``[files]`` settings for a Folders connection, as the discovery layer reads them."""
    roots = c["settings"]["roots"]
    return {"roots": [{"alias": r["alias"], "path": r["path"]} for r in roots],
            "providers": [{"id": c["source_id"], "adapter": "recoll", "roots": [r["alias"] for r in roots],
                           "confdir": str(Path(index_dir) / "recoll"), "python": "python3"}],
            "limits": {"timeout_seconds": 600, "max_output_bytes": 64_000_000}}


def with_files(config, index_dir: Path, role: str = connections.ROLE):
    """``config`` with the Folders connection as its files configuration, unless a settings file already
    configures file discovery (then that wins and folders chosen on the page are not used)."""
    import dataclasses

    if config.files is not None:
        return config
    c = get(config.store_dir, SID, role)
    if c is None or not c["settings"].get("roots"):
        return config  # mail-only instances never load file discovery
    from towpath.discovery.config import parse

    return dataclasses.replace(config, files=parse(files_table(c, index_dir), config.root, config.store_dir))


def write_conf(confdir: Path, roots: list[dict], scratch: Path, settings: dict | None = None) -> Path:
    o = {**DEFAULTS, **(settings or {})}
    confdir.mkdir(parents=True, exist_ok=True)
    topdirs = " ".join(json.dumps(r["path"]) for r in roots)  # Recoll accepts double-quoted paths
    conf = confdir / "recoll.conf"
    conf.write_text(f"""# Written by Towpath from the Folders page (folders and indexing settings); edits are replaced.
topdirs = {topdirs}
skippedNames = {o["skip"]}
noContentSuffixes+ = {o["name_only"]}
usesystemfilecommand = {int(bool(o["sniff"]))}
thrQSizes = 2 2 2
thrTCounts = {o["threads"]} {max(1, o["threads"] // 2)} 1
followLinks = 0
indexallfilenames = 1
textfilemaxmbs = {o["text_limit_mb"]}
compressedfilemaxkbs = {o["compressed_limit_mb"] * 1000}
idxflushmb = {o["batch_mb"]}
pdfocr = 0
loglevel = 2
""")
    return conf


def read_status(confdir: Path) -> dict:
    """Counts from Recoll's own status file. The current file name is never kept."""
    values = {}
    try:
        for line in (confdir / "idxstatus.txt").read_text().splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() in {"phase", "docsdone", "filesdone", "fileerrors", "dbtotdocs", "totfiles"}:
                values[key.strip()] = int(value.strip()) if value.strip().lstrip("-").isdigit() else value.strip()
    except OSError:
        pass
    return values


def _command(confdir: Path) -> list[str]:
    cmd = ["recollindex", "-c", str(confdir), "-k"]  # -k: retry files that failed before (a helper may now exist)
    if shutil.which("ionice"):
        cmd = ["ionice", "-c", "3"] + cmd  # idle I/O class where the kernel schedules I/O; ZFS ignores it
    return ["nice", "-n", "10"] + cmd if shutil.which("nice") else cmd


def _start(c: dict, data_dir: Path) -> subprocess.Popen:
    confdir, scratch = data_dir / "index" / "recoll", data_dir / "scratch" / "recoll"
    scratch.mkdir(parents=True, exist_ok=True)
    write_conf(confdir, c["settings"]["roots"], scratch, options(c))
    env = {**os.environ, "HOME": str(confdir), "TMPDIR": str(scratch), "RECOLL_TMPDIR": str(scratch)}
    log = open(confdir / "recollindex.log", "w")
    return subprocess.Popen(_command(confdir), stdout=log, stderr=subprocess.STDOUT, env=env,
                            start_new_session=True)


def _stop(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        os.killpg(proc.pid, signal.SIGTERM)  # Recoll flushes what it has indexed and exits
        try:
            proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()


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


def _unmeasured(config, c: dict) -> bool:
    from towpath import space

    if not store_path(config.store_dir, "files").exists():
        return False
    db = open_store(config.store_dir, "files", "connect")
    try:
        return bool(space.stale(db, SID, [r["alias"] for r in c["settings"].get("roots") or []]))
    finally:
        db.close()


def _measure(config, c: dict) -> list[dict]:
    """Measure space for every root whose newest import isn't measured yet (see towpath.space)."""
    from towpath import space

    roots = [r["alias"] for r in c["settings"].get("roots") or []]
    db = open_store(config.store_dir, "files", "connect")
    try:
        done = [{"source_id": SID, "step": "measured", "root": root,
                 "files": space.rebuild(db, SID, root, run)["files"]}
                for root, run in space.stale(db, SID, roots).items()]
        if done:
            space.find_duplicates(db, SID)
        return done
    finally:
        db.close()


def _in_background(name: str, work) -> None:
    def run():
        try:
            _outcomes[name] = {"result": work()}
        except Exception as exc:  # noqa: BLE001 - reported by the next poll; never log file names from a message
            _outcomes[name] = {"error": type(exc).__name__}

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
        return [{"source_id": SID, "step": "measured"}] if "result" in outcome else []
    if "error" in outcome:
        _update(config.store_dir, SID, "indexing-stopped", {"error": outcome["error"]}, indexing="idle",
                last_error="Adding files to search stopped with an error. Start again to resume.")
        return [{"source_id": SID, "step": "failed", "error": outcome["error"]}]
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
    was = (c.get("progress") or {}).get("phase")
    if proc is None and c["indexing"] == "running" and was in ("adding", "measuring"):
        # The worker restarted after Recoll had finished: carry on adding rather than walk every folder again.
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
