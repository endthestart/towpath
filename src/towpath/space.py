"""Where the space goes in indexed folders: folder sizes, space by type, largest files and common clutter.

The connector computes this after each import, from catalog rows alone (no file is opened), and the web service
reads it. Only top-level files count: an archive's or mailbox's members take no space of their own on disk.
Nothing here deletes or moves anything; it shows what could be cleaned up.
"""

import heapq
import json
from datetime import datetime, timezone

from towpath.canonical import digest

TOP_FOLDERS, TOP_FILES, TOP_TYPES = 25, 100, 40
VERSION = 2  # raised when a measurement holds something new, so every root is measured again

# A folder the owner keeps is never shown as clutter itself; clutter inside it still is. Marks live in the
# decisions store's settings, one row per folder: target ``space:<provider>:<root>:<path>``, key ``clutter``.
KEEP_KEY, KEEP_VALUE = "clutter", "keep"

# Files whose contents can hold other files or messages. Recoll indexes what it can read inside them as members of
# the outer file; a container with no indexed members may be empty, unreadable, encrypted, over a size limit or of
# a kind Recoll doesn't open, so the page says only that nothing inside is indexed.
CONTAINER_EXTENSIONS = {"zip", "rar", "7z", "tar", "tgz", "gz", "bz2", "tbz2", "xz", "zst", "cab", "iso", "jar",
                        "mbox", "mbx", "pst", "ost"}
CONTAINER_KINDS = {"archive-member": "archive", "mail-message": "mailbox"}  # the first member's kind
TOP_CONTAINERS = 100
SIZE_BUCKETS = ((100 * 1024 ** 2, "under 100 MB"), (1024 ** 3, "100 MB to 1 GB"), (None, "over 1 GB"))

# The month a file was last modified (UTC), from the catalog's file-modified date: Recoll lists it first, and an
# archive member's file-modified date is its archive's. Space's files per month and the search filter
# ``modified:`` both count by this date.
MODIFIED = ("(SELECT value FROM json_each(dates) WHERE json_extract(value, '$.meaning') = 'file-modified' "
            "LIMIT 1)")
MODIFIED_MONTH = f"substr(json_extract({MODIFIED}, '$.value'), 1, 7)"

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


def measure(rows, keep=(), containers=None) -> dict:
    """Space under every folder, by type and by month last modified, the largest files and clutter, from
    (rel_path, size) or (rel_path, size, month) rows. Folders in ``keep`` are not clutter themselves.
    ``containers`` maps a file's path to [indexed members, kind] (see ``rebuild``)."""
    containers = containers or {}
    holding: list[tuple[int, str, int, str]] = []  # containers with indexed members, largest kept
    held = [0, 0, 0]  # containers with indexed members: files, bytes, members
    empty = {label: [0, 0] for _, label in SIZE_BUCKETS}  # container files with no indexed members, by size
    folders: dict[str, list[int]] = {"": [0, 0]}
    types: dict[str, list[int]] = {}
    months: dict[str, list[int]] = {}
    undated = [0, 0]
    largest: list[tuple[int, str]] = []
    loose: dict[str, dict[str, list[int]]] = {}  # file clutter by kind, then by the folder holding it
    files = total = unknown = 0
    for row in rows:
        rel_path, size, month = row[0], row[1], row[2] if len(row) > 2 else None
        if size is None:
            unknown += 1
        size = size or 0
        files += 1
        total += size
        entry = months.get(month) if month else undated
        if entry is None:
            months[month] = [1, size]
        else:
            entry[0] += 1
            entry[1] += size
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
        inside = containers.get(rel_path)
        if inside is not None:
            held[0] += 1
            held[1] += size
            held[2] += inside[0]
            item = (size, rel_path, inside[0], inside[1])
            if len(holding) < TOP_CONTAINERS:
                heapq.heappush(holding, item)
            elif item > holding[0]:
                heapq.heappushpop(holding, item)
        elif ext in CONTAINER_EXTENSIONS:
            bucket = next(label for limit, label in SIZE_BUCKETS if limit is None or size < limit)
            empty[bucket][0] += 1
            empty[bucket][1] += size
    return {"folders": folders, "files": files, "bytes": total, "unknown_size": unknown,
            "types": sorted(([ext, n, b] for ext, (n, b) in types.items()), key=lambda t: (-t[2], t[0]))[:TOP_TYPES],
            "largest": [[path, size] for size, path in sorted(largest, key=lambda t: (-t[0], t[1]))],
            "clutter": _clutter(folders, loose, frozenset(keep)),
            "months": [[m, n, b] for m, (n, b) in sorted(months.items())], "undated": undated,
            "containers": {"held": held, "top": [[path, size, n, kind] for size, path, n, kind in
                                                  sorted(holding, key=lambda t: (-t[0], t[1]))],
                           "unindexed": [[label, *empty[label]] for _, label in SIZE_BUCKETS]}}


def _inside(path: str, outer: set[str]) -> bool:
    """Whether ``path`` is one of ``outer`` or lies inside one of them."""
    while path:
        if path in outer:
            return True
        cut = path.rfind("/")
        path = path[:cut] if cut >= 0 else ""
    return "" in outer


def _clutter(folders: dict[str, list[int]], loose: dict[str, dict[str, list[int]]],
             keep: frozenset[str] = frozenset()) -> list[dict]:
    telling = {name for needs in SYSTEM_COPIES.values() for need in needs for name in need}
    children: dict[str, set[str]] = {}  # only the folder names that identify a system copy
    for path in folders:
        low = _name(path).lower()
        if path and low in telling:
            children.setdefault(_parent(path), set()).add(low)
    matched: dict[str, set[str]] = {}
    for path in sorted(folders, key=lambda p: (p.count("/"), p)):  # outermost first
        if not path or path in keep:
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


def keep_target(provider_id: str, root: str, path: str) -> str:
    return f"space:{provider_id}:{root}:{path}"


def marks(decisions_db, provider_id: str) -> dict[str, list[str]]:
    """The folders the owner keeps, by root (from an open decisions store, any role)."""
    prefix = keep_target(provider_id, "", "")[:-2]
    out: dict[str, list[str]] = {}
    for (target,) in decisions_db.execute(
            "SELECT target_id FROM settings WHERE key = ? AND value = ? AND substr(target_id, 1, ?) = ?",
            (KEEP_KEY, KEEP_VALUE, len(prefix) + 1, prefix + ":")):
        root, _, path = target[len(prefix) + 1:].partition(":")
        out.setdefault(root, []).append(path)
    return {root: sorted(paths) for root, paths in out.items()}


def marks_digest(paths) -> str:
    return digest(sorted(paths))[:16]


def rebuild(db, provider_id: str, root: str, run_id: str, keep=()) -> dict:
    """Measure one root from the catalog and replace what was stored for it. Returns the summary."""
    containers: dict[str, list] = {}  # outer file -> [indexed members at any depth, kind of the first level]
    for rel_path, first in db.execute(
            "SELECT rel_path, json_extract(members, '$[0].kind') FROM occurrences WHERE provider_id = ? "
            "AND root_alias = ? AND missing_since_run IS NULL AND members != '[]'", (provider_id, root)):
        entry = containers.get(rel_path)
        if entry is None:
            kind = CONTAINER_KINDS.get(first)
            if kind is None:
                continue  # parts embedded in a document (images in an office file): not a container
            containers[rel_path] = [1, kind]
        else:
            entry[0] += 1
    rows = db.execute(f"SELECT rel_path, size, {MODIFIED_MONTH} FROM occurrences WHERE provider_id = ? "
                      "AND root_alias = ? AND missing_since_run IS NULL AND members = '[]'", (provider_id, root))
    measured = measure(rows, keep, containers)
    folders = measured.pop("folders")
    db.execute("DELETE FROM space_folders WHERE provider_id = ? AND root_alias = ?", (provider_id, root))
    db.executemany("INSERT INTO space_folders (provider_id, root_alias, path, parent, files, bytes) "
                   "VALUES (?,?,?,?,?,?)",
                   ((provider_id, root, path, _parent(path), n, b) for path, (n, b) in folders.items()))
    summary = {**measured, "folders": len(folders) - 1, "v": VERSION, "marks": marks_digest(keep),
               "kept": [[path, *folders.get(path, [0, 0])] for path in sorted(keep)]}
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


def stale(db, provider_id: str, roots: list[str], keep: dict[str, list[str]] | None = None) -> dict[str, str]:
    """Roots to measure again, with their newest import's run ID: that import isn't measured yet, the measurement
    is from an earlier version, or (when ``keep`` is given) the owner's kept folders changed since."""
    measured = {root: (run, v, digest_) for root, run, v, digest_ in db.execute(
        "SELECT root_alias, run_id, json_extract(body, '$.v'), json_extract(body, '$.marks') FROM space_summary "
        "WHERE provider_id = ?", (provider_id,))}
    latest = latest_imports(db, provider_id)
    out = {}
    for root in roots:
        if root not in latest:
            continue
        run, version, kept = measured.get(root, (None, None, None))
        if (run != latest[root] or version != VERSION
                or (keep is not None and kept != marks_digest(keep.get(root, ())))):
            out[root] = latest[root]
    return out


DUPLICATE_MIN_BYTES = 1024 * 1024  # smaller copies rarely matter for space
TOP_DUPLICATES, COPIES_SHOWN = 100, 8
_NAME = "lower(substr(rel_path, length(rtrim(rel_path, replace(rel_path, '/', ''))) + 1))"


def find_duplicates(db, provider_id: str) -> dict:
    """Files with the same name and size in more than one place, across all roots, largest waste first. A shared
    name and size is a strong hint, not proof: the page says to compare before deleting."""
    db.execute("PRAGMA temp_store = MEMORY")  # the grouping must not need a writable temp folder
    # Backup sets store fixed-size chunks with repeating numbered names; comparing those would bury real copies.
    inside_sets = " OR ".join(f"rel_path LIKE '%{s}/%'" for s in FOLDER_SUFFIXES["backups"])
    present = (f"provider_id = ? AND missing_since_run IS NULL AND members = '[]' AND size >= ? "
               f"AND NOT ({inside_sets})", (provider_id, DUPLICATE_MIN_BYTES))
    groups = db.execute(f"SELECT {_NAME} AS name, size, count(*) AS n FROM occurrences WHERE {present[0]} "
                        "GROUP BY size, name HAVING n > 1", present[1]).fetchall()
    wasted = sum((n - 1) * size for _, size, n in groups)
    top = sorted(groups, key=lambda g: (-(g[2] - 1) * g[1], g[0]))[:TOP_DUPLICATES]
    wanted = {(name, size) for name, size, _ in top}
    copies: dict[tuple[str, int], list] = {}
    sizes = sorted({size for _, size in wanted})
    for start in range(0, len(sizes), 500):  # one pass per 500 sizes, never one per group
        chunk = sizes[start:start + 500]
        for root, rel, name, size in db.execute(
                f"SELECT root_alias, rel_path, {_NAME}, size FROM occurrences WHERE {present[0]} "
                f"AND size IN ({','.join('?' * len(chunk))})", (*present[1], *chunk)):
            if (name, size) in wanted:
                copies.setdefault((name, size), []).append([root, rel])
    body = {"groups": len(groups), "files": sum(n for _, _, n in groups), "wasted": wasted,
            "top": [{"name": next((p[1].rpartition("/")[2] for p in copies.get((name, size), [])), name),
                     "size": size, "copies": n, "wasted": (n - 1) * size,
                     "places": sorted(copies.get((name, size), []))[:COPIES_SHOWN]} for name, size, n in top]}
    db.execute("INSERT OR REPLACE INTO space_duplicates (provider_id, body, updated_at) VALUES (?,?,?)",
               (provider_id, json.dumps(body, separators=(",", ":")), _now()))
    db.commit()
    return body


# -- reading, for the web service ------------------------------------------------------------------------------


def summaries(db, provider_id: str, roots: list[str]) -> dict[str, dict]:
    out = {}
    for root in roots:
        row = db.execute("SELECT body, updated_at FROM space_summary WHERE provider_id = ? AND root_alias = ?",
                         (provider_id, root)).fetchone()
        if row is not None:
            out[root] = {**json.loads(row[0]), "updated_at": row[1]}
    return out


FILES_LISTED_UP_TO = 50_000  # folders holding more (with subfolders) list subfolders only, to stay fast


def files_here(db, provider_id: str, root: str, path: str, limit: int = 100) -> list[list]:
    """The largest files directly in one folder: [[occurrence_id, name, size], ...]. The trigram index narrows to
    rows mentioning the folder's path; the exact prefix check decides."""
    prefix = f"{path}/" if path else ""
    where = ("o.provider_id = ? AND o.root_alias = ? AND o.missing_since_run IS NULL AND o.members = '[]' "
             "AND substr(o.rel_path, 1, ?) = ? AND instr(substr(o.rel_path, ? + 1), '/') = 0")
    args = [provider_id, root, len(prefix), prefix, len(prefix)]
    if len(prefix) >= 3:
        sql = ("SELECT o.occurrence_id, o.rel_path, o.size FROM occurrences_text t JOIN occurrences o "
               f"ON o.rowid = t.rowid WHERE t.text MATCH ? AND {where} ORDER BY o.size DESC LIMIT ?")
        args = ['"' + prefix.replace('"', '""') + '"', *args]
    else:
        sql = (f"SELECT o.occurrence_id, o.rel_path, o.size FROM occurrences o WHERE {where} "
               "ORDER BY o.size DESC LIMIT ?")
    return [[occ, rel[len(prefix):], size] for occ, rel, size in db.execute(sql, (*args, limit))]


def duplicates(db, provider_id: str) -> dict | None:
    row = db.execute("SELECT body, updated_at FROM space_duplicates WHERE provider_id = ?", (provider_id,)).fetchone()
    return {**json.loads(row[0]), "updated_at": row[1]} if row else None


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
