"""Where the space goes in indexed folders: folder sizes, space by type, largest files and common clutter.

The connector computes this after each import, from catalog rows alone (no file is opened), and the web service
reads it. Only top-level files count: an archive's or mailbox's members take no space of their own on disk.
Nothing here deletes or moves anything; it shows what could be cleaned up.
"""

import heapq
import json
from datetime import datetime, timezone

TOP_FOLDERS, TOP_FILES, TOP_TYPES = 25, 100, 40

# Folders that are usually safe to delete or rebuild, by name (compared case-insensitively). Only the outermost
# match counts, so a node_modules inside another node_modules isn't counted twice.
FOLDER_KINDS = {
    "packages": {"node_modules", "bower_components", "site-packages", ".venv", "venv"},
    "git": {".git"},
    "caches": {".cache", "cache", "caches", "__pycache__", ".pytest_cache", ".gradle", ".npm", ".yarn"},
    "thumbnails": {"@eadir", ".thumbnails"},
    "metadata": {".spotlight-v100", ".fseventsd", ".trashes", ".temporaryitems", ".documentrevisions-v100"},
    "trash": {"#recycle", "$recycle.bin", "recycler", ".trash", ".recycle"},
    "windows": {"program files", "program files (x86)", "programdata", "system volume information",
                "$windows.~bt", "$windows.~ws", "windows.old", "$sysreset", "msocache", "perflogs"},
    "temp": {"tmp", "temp"},
    "backups": set(),  # by suffix, below
}
FOLDER_PREFIXES = {"trash": (".trash-",)}
FOLDER_SUFFIXES = {"backups": (".hbk", ".sparsebundle", ".backupdb")}
# Whole copies of a system disk, recognised by the folders side by side in them.
SYSTEM_COPIES = {
    "windows": ({"windows", "system32"},),  # a "Windows" folder holding System32 (checked below)
    "linux": ({"etc", "usr", "var"}, {"bin", "etc", "usr"}),
    "macos": ({"system", "library", "applications"},),
}
FILE_KINDS = {"thumbnails": {"thumbs.db", "ehthumbs.db"}, "metadata": {".ds_store", "desktop.ini", ".localized"}}
FILE_PREFIXES = {"metadata": ("._",), "temp": ("~$",)}
FILE_SUFFIXES = {"temp": (".tmp", ".temp", ".swp", ".part", ".crdownload")}

LABELS = {
    "packages": ("Code packages", "Downloaded libraries such as node_modules; the projects can fetch them again."),
    "git": ("Git history", "The .git folders of repositories: the full history of each one."),
    "caches": ("Caches", "Files apps keep to start faster; they are rebuilt when needed."),
    "thumbnails": ("Thumbnails", "Previews made by Synology, Windows and Linux file managers."),
    "metadata": ("System metadata", "Files such as .DS_Store and desktop.ini that Macs and Windows leave behind."),
    "trash": ("Recycle bins and trash", "Deleted files that were never emptied."),
    "windows": ("Windows system files", "Copies of Windows itself and of installed programs."),
    "linux": ("Linux system copies", "Folders holding a whole Linux system (etc, usr, var side by side)."),
    "macos": ("macOS system copies", "Folders holding a whole Mac system (System, Library, Applications)."),
    "temp": ("Temporary files", "Temp folders, unfinished downloads and Office lock files."),
    "backups": ("Backup sets", "Hyper Backup, Time Machine and disk-image backups: large, often superseded."),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parent(path: str) -> str | None:
    if not path:
        return None
    cut = path.rfind("/")
    return path[:cut] if cut >= 0 else ""


def _name(path: str) -> str:
    return path[path.rfind("/") + 1:]


def _extension(name: str) -> str:
    dot = name.rfind(".")
    ext = name[dot + 1:].lower() if dot > 0 else ""
    return ext if ext and len(ext) <= 10 and ext.isalnum() else ""


def _file_kind(name: str) -> str | None:
    low = name.lower()
    for kind, names in FILE_KINDS.items():
        if low in names:
            return kind
    for kind, prefixes in FILE_PREFIXES.items():
        if low.startswith(prefixes):
            return kind
    for kind, suffixes in FILE_SUFFIXES.items():
        if low.endswith(suffixes):
            return kind
    return None


def _folder_kind(name: str) -> str | None:
    low = name.lower()
    for kind, names in FOLDER_KINDS.items():
        if low in names:
            return kind
    for kind, prefixes in FOLDER_PREFIXES.items():
        if low.startswith(prefixes):
            return kind
    for kind, suffixes in FOLDER_SUFFIXES.items():
        if low.endswith(suffixes):
            return kind
    return None


def measure(rows) -> dict:
    """Space under every folder, by type, the largest files and clutter, from (rel_path, size) rows."""
    folders: dict[str, list[int]] = {"": [0, 0]}
    types: dict[str, list[int]] = {}
    largest: list[tuple[int, str]] = []
    loose: dict[str, dict[str, list[int]]] = {}  # file clutter by kind, then by the folder holding it
    files = total = unknown = 0
    for rel_path, size in rows:
        if size is None:
            unknown += 1
        size = size or 0
        files += 1
        total += size
        root = folders[""]
        root[0] += 1
        root[1] += size
        cut = rel_path.find("/")
        while cut >= 0:
            prefix = rel_path[:cut]
            entry = folders.get(prefix)
            if entry is None:
                folders[prefix] = [1, size]
            else:
                entry[0] += 1
                entry[1] += size
            cut = rel_path.find("/", cut + 1)
        name = _name(rel_path)
        ext = _extension(name)
        entry = types.get(ext)
        if entry is None:
            types[ext] = [1, size]
        else:
            entry[0] += 1
            entry[1] += size
        if len(largest) < TOP_FILES:
            heapq.heappush(largest, (size, rel_path))
        elif size > largest[0][0]:
            heapq.heappushpop(largest, (size, rel_path))
        kind = _file_kind(name)
        if kind is not None:
            where = loose.setdefault(kind, {}).setdefault(_parent(rel_path) or "", [0, 0])
            where[0] += 1
            where[1] += size
    return {"folders": folders, "files": files, "bytes": total, "unknown_size": unknown,
            "types": sorted(([ext, n, b] for ext, (n, b) in types.items()), key=lambda t: (-t[2], t[0]))[:TOP_TYPES],
            "largest": [[path, size] for size, path in sorted(largest, key=lambda t: (-t[0], t[1]))],
            "clutter": _clutter(folders, loose)}


def _inside(path: str, outer: set[str]) -> bool:
    """Whether ``path`` is one of ``outer`` or lies inside one of them."""
    while path:
        if path in outer:
            return True
        cut = path.rfind("/")
        path = path[:cut] if cut >= 0 else ""
    return "" in outer


def _clutter(folders: dict[str, list[int]], loose: dict[str, dict[str, list[int]]]) -> list[dict]:
    telling = {name for needs in SYSTEM_COPIES.values() for need in needs for name in need}
    children: dict[str, set[str]] = {}  # only the folder names that identify a system copy
    for path in folders:
        low = _name(path).lower()
        if path and low in telling:
            children.setdefault(_parent(path), set()).add(low)
    matched: dict[str, set[str]] = {}
    for path in sorted(folders, key=lambda p: (p.count("/"), p)):  # outermost first
        if not path:
            continue
        name = _name(path)
        kinds = [k for k in (_folder_kind(name),) if k]
        below = children.get(path, set())
        if name.lower() == "windows" and "system32" in below:
            kinds.append("windows")
        for kind, needs in SYSTEM_COPIES.items():
            if kind != "windows" and any(need <= below for need in needs):
                kinds.append(kind)
        for kind in kinds:
            found = matched.setdefault(kind, set())
            if not _inside(_parent(path), found):
                found.add(path)
    out = []
    for kind in LABELS:
        found = matched.get(kind, set())
        tops = [[path, *folders[path]] for path in found]
        tops += [[where, n, b] for where, (n, b) in (loose.get(kind) or {}).items() if not _inside(where, found)]
        if not tops:
            continue
        tops.sort(key=lambda t: (-t[2], t[0]))
        out.append({"kind": kind, "label": LABELS[kind][0], "about": LABELS[kind][1], "places": len(tops),
                    "files": sum(t[1] for t in tops), "bytes": sum(t[2] for t in tops), "top": tops[:TOP_FOLDERS]})
    out.sort(key=lambda c: -c["bytes"])
    return out


def rebuild(db, provider_id: str, root: str, run_id: str) -> dict:
    """Measure one root from the catalog and replace what was stored for it. Returns the summary."""
    rows = db.execute("SELECT rel_path, size FROM occurrences WHERE provider_id = ? AND root_alias = ? "
                      "AND missing_since_run IS NULL AND members = '[]'", (provider_id, root))
    measured = measure((r[0], r[1]) for r in rows)
    folders = measured.pop("folders")
    db.execute("DELETE FROM space_folders WHERE provider_id = ? AND root_alias = ?", (provider_id, root))
    db.executemany("INSERT INTO space_folders (provider_id, root_alias, path, parent, files, bytes) "
                   "VALUES (?,?,?,?,?,?)",
                   ((provider_id, root, path, _parent(path), n, b) for path, (n, b) in folders.items()))
    summary = {**measured, "folders": len(folders) - 1}
    db.execute("INSERT OR REPLACE INTO space_summary (provider_id, root_alias, run_id, body, updated_at) "
               "VALUES (?,?,?,?,?)", (provider_id, root, run_id, json.dumps(summary, separators=(",", ":")), _now()))
    db.commit()
    return summary


def latest_imports(db, provider_id: str) -> dict[str, str]:
    """The newest finished import run of each root, from the coverage it recorded."""
    rows = db.execute("SELECT c.root_alias, c.run_id FROM coverage c JOIN runs r ON r.run_id = c.run_id "
                      "WHERE c.provider_id = ? AND r.kind = 'import' AND r.finished_at IS NOT NULL "
                      "ORDER BY r.finished_at", (provider_id,)).fetchall()
    return {root: run for root, run in rows}


def stale(db, provider_id: str, roots: list[str]) -> dict[str, str]:
    """Roots whose newest import isn't measured yet, with that import's run ID."""
    measured = dict(db.execute("SELECT root_alias, run_id FROM space_summary WHERE provider_id = ?",
                               (provider_id,)).fetchall())
    latest = latest_imports(db, provider_id)
    return {root: latest[root] for root in roots if root in latest and measured.get(root) != latest[root]}


# -- reading, for the web service ------------------------------------------------------------------------------


def summaries(db, provider_id: str, roots: list[str]) -> dict[str, dict]:
    out = {}
    for root in roots:
        row = db.execute("SELECT body, updated_at FROM space_summary WHERE provider_id = ? AND root_alias = ?",
                         (provider_id, root)).fetchone()
        if row is not None:
            out[root] = {**json.loads(row[0]), "updated_at": row[1]}
    return out


def folder(db, provider_id: str, root: str, path: str, limit: int = 200) -> dict | None:
    """One folder's total and its subfolders, largest first."""
    row = db.execute("SELECT files, bytes FROM space_folders WHERE provider_id = ? AND root_alias = ? AND path = ?",
                     (provider_id, root, path)).fetchone()
    if row is None:
        return None
    subs = db.execute("SELECT path, files, bytes FROM space_folders WHERE provider_id = ? AND root_alias = ? "
                      "AND parent = ? ORDER BY bytes DESC, path LIMIT ?", (provider_id, root, path, limit)).fetchall()
    count = db.execute("SELECT count(*), coalesce(sum(files), 0), coalesce(sum(bytes), 0) FROM space_folders "
                       "WHERE provider_id = ? AND root_alias = ? AND parent = ?", (provider_id, root, path)).fetchone()
    return {"path": path, "files": row[0], "bytes": row[1], "subfolders": [list(s) for s in subs],
            "subfolder_count": count[0], "here_files": row[0] - count[1], "here_bytes": row[1] - count[2]}
