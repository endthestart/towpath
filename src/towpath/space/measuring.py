"""Measuring a catalog's folders: sizes, containers, and folders that look like clutter."""

import heapq
from datetime import datetime, timezone

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
