"""What the owner chooses: the folders to search, and how they are indexed."""

import json
import os
import re
import subprocess
import threading
from contextlib import closing
from pathlib import Path

from towpath import connections
from towpath.connections import ConnectionProblem, _now, _update, get
from towpath.stores import open_store

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
