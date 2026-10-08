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
import time
from contextlib import closing
from pathlib import Path

from towpath import connections
from towpath.connections import ConnectionProblem, _now, _update, get
from towpath.stores import open_store

SID = "folders"
PAGE = 5000
LIBRARY_ENV = "TOWPATH_LIBRARY"
# Files Recoll should index by name only: photos, video, audio, disk images and binaries have no useful text,
# and reading them all would only add load on the pool.
NAME_ONLY = (".jpg .jpeg .png .gif .heic .heif .webp .bmp .tif .tiff .nef .cr2 .cr3 .arw .dng .raf .orf .rw2 .psd "
             ".mp4 .mov .avi .mkv .m4v .wmv .mpg .mpeg .3gp .mts .m2ts .mp3 .flac .wav .m4a .aac .ogg .opus .aiff "
             ".iso .img .dmg .vmdk .qcow2 .vdi .vhd .vhdx .bin .exe .dll .so .dylib .o .a .class .jar .pyc .db "
             ".sqlite .sparsebundle .band")
SKIPPED_NAMES = ("@eaDir #recycle .zfs .snapshot .Trash* .DS_Store Thumbs.db desktop.ini node_modules .git "
                 "__pycache__ .cache *.tmp *~")
_running: dict[str, subprocess.Popen] = {}


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
    settings = {"roots": roots, "username": None}
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


def write_conf(confdir: Path, roots: list[dict], scratch: Path) -> Path:
    confdir.mkdir(parents=True, exist_ok=True)
    topdirs = " ".join(json.dumps(r["path"]) for r in roots)  # Recoll accepts double-quoted paths
    conf = confdir / "recoll.conf"
    conf.write_text(f"""# Written by Towpath from the folders chosen on the Connections page; edits are replaced.
topdirs = {topdirs}
skippedNames+ = {SKIPPED_NAMES}
noContentSuffixes+ = {NAME_ONLY}
followLinks = 0
indexallfilenames = 1
textfilemaxmbs = 20
compressedfilemaxkbs = 100000
idxflushmb = 50
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
        cmd = ["ionice", "-c", "3"] + cmd  # idle I/O class: Recoll reads only when the pool is otherwise quiet
    return ["nice", "-n", "10"] + cmd if shutil.which("nice") else cmd


def _start(c: dict, data_dir: Path) -> subprocess.Popen:
    confdir, scratch = data_dir / "index" / "recoll", data_dir / "scratch" / "recoll"
    scratch.mkdir(parents=True, exist_ok=True)
    write_conf(confdir, c["settings"]["roots"], scratch)
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


def _progress(status: dict, phase: str, imported: int | None = None) -> dict:
    return {"phase": phase, "files": status.get("filesdone"), "docs": status.get("docsdone"),
            "errors": status.get("fileerrors"), "total": status.get("totfiles"),
            "indexed": imported if imported is not None else status.get("dbtotdocs"), "rate": None, "at": _now()}


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


def index_pending(config, data_dir: Path) -> list[dict]:
    """One step of Folders indexing per poll: start Recoll, report its progress, stop it on Pause, and when it
    finishes add the index to search. Never starts anything the owner didn't ask for."""
    c = get(config.store_dir, SID)
    if c is None or c["state"] != "ready":
        return []
    confdir = Path(data_dir) / "index" / "recoll"
    proc = _running.get(SID)
    if c["indexing"] == "paused":
        if proc is not None:
            _stop(proc)
            _running.pop(SID, None)
            return [{"source_id": SID, "step": "paused"}]
        return []
    if c["indexing"] not in ("requested", "running"):
        return []
    if proc is None:
        _running[SID] = _start(c, Path(data_dir))
        _update(config.store_dir, SID, "indexing-started" if c["indexing"] == "requested" else "indexing-resumed",
                indexing="running", last_error=None, progress=_progress(read_status(confdir), "reading"))
        return [{"source_id": SID, "step": "started"}]
    if proc.poll() is None:
        _update(config.store_dir, SID, progress=_progress(read_status(confdir), "reading"))
        return [{"source_id": SID, "step": "reading"}]
    _running.pop(SID, None)
    status = read_status(confdir)
    if proc.returncode != 0:
        _update(config.store_dir, SID, "indexing-stopped", {"exit": proc.returncode}, indexing="idle",
                last_error=f"Recoll stopped with an error (exit {proc.returncode}); its log is in the index folder. "
                           "Start again to resume.")
        return [{"source_id": SID, "step": "failed", "exit": proc.returncode}]
    _update(config.store_dir, SID, progress=_progress(status, "adding", 0))
    started = time.monotonic()
    result = _import(config, c, Path(data_dir), status)
    if (get(config.store_dir, SID) or {}).get("indexing") == "paused":
        return [{"source_id": SID, "step": "paused-while-adding"}]
    _update(config.store_dir, SID, "indexed", {"files": result["seen"], "seconds": round(time.monotonic() - started)},
            indexing="idle", progress=_progress(status, "done", result["seen"]))
    return [{"source_id": SID, "step": "indexed", "files": result["seen"]}]
