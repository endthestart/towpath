"""Apple Photos libraries inside the chosen folders: which files are originals the library shows, and which it hides.

A library (``*.photoslibrary``) keeps its photos under ``originals/`` and its own previews and edits elsewhere. Its
database (``database/Photos.sqlite``) says which photos the owner hid or deleted. Towpath reads that database
read-only (and never anything else of a hidden photo), and records in the files store every file of the library that
isn't an original the library shows: hidden or deleted photos, and the library's own previews and edits. Those are
never previewed and are left out of photo lists. If the database can't be read, every file of that library is left
out until it can.
"""

import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from towpath.stores import open_store, store_path

ROLE = "connect"
SUFFIX = ".photoslibrary"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def library_of(rel_path: str) -> str | None:
    """The library folder (relative to its root) a file is in, or None."""
    head, sep, _ = rel_path.partition(SUFFIX + "/")
    return head + SUFFIX if sep else None


def read_library(db_path: Path) -> tuple[set[str], set[str]] | None:
    """(originals the library shows, as paths inside the library; UUIDs of photos it hides or deleted), or None
    when its database can't be read as a Photos database."""
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True)
    except sqlite3.Error:
        return None
    try:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        table = next((t for t in ("ZASSET", "ZGENERICASSET") if t in tables), None)
        if table is None:
            return None
        columns = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
        if not {"ZHIDDEN", "ZDIRECTORY", "ZFILENAME", "ZUUID"} <= columns:
            return None
        trashed = "ZTRASHEDSTATE" if "ZTRASHEDSTATE" in columns else "0"
        visible, hidden = set(), set()
        for is_hidden, is_trashed, directory, filename, uuid in con.execute(
                f"SELECT ZHIDDEN, {trashed}, ZDIRECTORY, ZFILENAME, ZUUID FROM {table}"):
            if is_hidden or is_trashed:
                if uuid:
                    hidden.add(str(uuid))
            elif directory and filename:
                visible.add(f"originals/{directory}/{filename}")
        return visible, hidden
    except sqlite3.Error:
        return None
    finally:
        con.close()


def mark_hidden(config) -> list[dict]:
    """Bring the hidden-file list up to date for every library whose database changed since it was last read."""
    files = getattr(config, "files", None)
    if files is None or not store_path(config.store_dir, "files").exists():
        return []
    roots = {alias: Path(r.path) for alias, r in files.roots.items()}
    out = []
    with closing(open_store(config.store_dir, "files", ROLE)) as db:
        libs: dict[tuple[str, str], list[tuple[str, str]]] = {}
        for oid, alias, rel in db.execute("SELECT occurrence_id, root_alias, rel_path FROM occurrences WHERE "
                                          "rel_path LIKE ? AND missing_since_run IS NULL", (f"%{SUFFIX}/%",)):
            lib = library_of(rel)
            if lib and alias in roots:
                libs.setdefault((alias, lib), []).append((oid, rel))
        changed = False
        for (alias, lib), members in libs.items():
            database = roots[alias] / lib / "database" / "Photos.sqlite"
            try:
                stamp = f"{database.stat().st_mtime_ns}:{len(members)}"
            except OSError:
                stamp = f"missing:{len(members)}"
            key = f"apple:{alias}:{lib}"
            seen = db.execute("SELECT value FROM photo_progress WHERE key = ?", (key,)).fetchone()
            if seen and seen[0] == stamp:
                continue
            found = read_library(database) if not stamp.startswith("missing") else None
            ids = [oid for oid, _ in members]
            for start in range(0, len(ids), 900):
                chunk = ids[start:start + 900]
                db.execute(f"DELETE FROM photo_hidden WHERE occurrence_id IN ({','.join('?' for _ in chunk)})", chunk)
            rows = []
            for oid, rel in members:
                inside = rel[len(lib) + 1:]
                if found is None:
                    rows.append((oid, "the library's database couldn't be read"))
                elif inside not in found[0]:
                    name = inside.rpartition("/")[2]
                    rows.append((oid, "hidden or deleted in Apple Photos" if any(name.startswith(u) for u in found[1])
                                 or inside.startswith("originals/") else "the library's own copy"))
            db.executemany("INSERT OR REPLACE INTO photo_hidden VALUES (?,?,?)", [(o, w, now()) for o, w in rows])
            db.execute("INSERT OR REPLACE INTO photo_progress VALUES (?,?,?)", (key, stamp, now()))
            changed = True
            out.append({"step": "apple-photos-checked", "files": len(members), "left_out": len(rows),
                        "readable": found is not None})
        if changed:
            version = int((db.execute("SELECT value FROM photo_progress WHERE key = 'hidden_version'").fetchone()
                           or ["0"])[0]) + 1
            db.execute("INSERT OR REPLACE INTO photo_progress VALUES ('hidden_version', ?, ?)", (str(version), now()))
        db.commit()
    return out


def checked_libraries(db) -> set[tuple[str, str]]:
    """(root, library) pairs whose database has been read (or found unreadable) at least once."""
    return {tuple(r[0][len("apple:"):].split(":", 1)) for r in db.execute(
        "SELECT key FROM photo_progress WHERE key LIKE 'apple:%'")}
