"""Reading photos' metadata from the catalog in slices, and keeping the owner's private photos out of it."""

import json
import os
import re
import subprocess
import tempfile
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from towpath.config import PRIVATE_EXPORT, PRIVATE_EXPORT_GLOBS
from towpath.stores import open_store, store_path

ROLE = "connect"
TYPES = ("image/jpeg", "image/jpg", "image/pjpeg", "image/heic", "image/heif", "image/tiff", "image/x-canon-cr2",
         "image/x-nikon-nef", "image/x-sony-arw", "image/x-adobe-dng", "image/x-fuji-raf", "image/x-panasonic-rw2")
EXTENSIONS = (".jpg", ".jpeg", ".heic", ".heif", ".tif", ".tiff", ".nef", ".cr2", ".cr3", ".arw", ".dng", ".raf",
              ".rw2", ".orf")
PRIVATE = PRIVATE_EXPORT  # the owner's private photo export (defined with the configuration)
PRIVATE_GLOBS = PRIVATE_EXPORT_GLOBS
TAGS = ("-Make", "-Model", "-LensModel", "-LensID", "-DateTimeOriginal", "-CreateDate", "-GPSLatitude",
        "-GPSLongitude", "-ImageWidth", "-ImageHeight")
BATCH = 200
FIRST_YEAR = 1826  # the oldest surviving photograph; earlier dates are a clock never set
SCAN = 20_000  # catalog rows looked at per batch while finding photos
SLICE_SECONDS = 60
TIMEOUT = 300


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def is_private(path) -> bool:
    """Whether a path (relative or absolute) is in the owner's private export: never read, hashed, shown or
    indexed for its content."""
    return bool(PRIVATE.search(str(path)))


def private_in(config, alias: str, rel_path: str) -> bool:
    """``is_private`` for a file in a configured root, tested on its full path (a root may be the export itself, or
    sit inside it). A file whose root is no longer configured counts as private: it isn't read either."""
    roots = getattr(getattr(config, "files", None), "roots", None) or {}
    root = roots.get(alias)
    return root is None or is_private(f"{root.path}/{rel_path}")


def is_photo(rel_path: str, media_type: str | None) -> bool:
    if is_private(rel_path):
        return False
    return (media_type or "").lower() in TYPES or rel_path.lower().endswith(EXTENSIONS)


def _taken(value) -> str | None:
    """EXIF ``2019:05:14 10:15:00`` as ``2019-05-14T10:15:00`` (local time as the camera recorded it)."""
    m = re.match(r"(\d{4}):(\d{2}):(\d{2})[ T](\d{2}):(\d{2}):(\d{2})", str(value or ""))
    if not m or not FIRST_YEAR <= int(m.group(1)) <= datetime.now(timezone.utc).year + 1:
        return None  # unset, or a camera clock that was never set (0000, 4501)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}T{m.group(4)}:{m.group(5)}:{m.group(6)}"


def read_headers(paths: list[Path], exiftool: str = "exiftool") -> dict[str, dict]:
    """ExifTool's reading of each file's headers, keyed by path. Files it can't read are left out."""
    if not paths:
        return {}
    with tempfile.NamedTemporaryFile("w", suffix=".args", delete=False) as args:
        args.write("\n".join(str(p) for p in paths))
    try:
        proc = subprocess.run([exiftool, "-json", "-n", "-fast2", "-q", "-q", "-charset", "filename=utf8", *TAGS,
                               "-@", args.name], capture_output=True, timeout=TIMEOUT, check=False)
    finally:
        os.unlink(args.name)
    try:
        rows = json.loads(proc.stdout.decode("utf-8", "replace") or "[]")
    except ValueError:
        return {}
    return {r.get("SourceFile"): r for r in rows if r.get("SourceFile")}


def _record(row: dict | None) -> dict:
    if row is None:
        return {"problem": "unreadable"}
    lat, lon = row.get("GPSLatitude"), row.get("GPSLongitude")
    located = isinstance(lat, (int, float)) and isinstance(lon, (int, float)) and (lat, lon) != (0, 0)
    return {"taken": _taken(row.get("DateTimeOriginal")) or _taken(row.get("CreateDate")),
            "make": (str(row.get("Make") or "").strip() or None),
            "model": (str(row.get("Model") or "").strip() or None),
            "lens": (str(row.get("LensModel") or row.get("LensID") or "").strip() or None),
            "lat": lat if located else None, "lon": lon if located else None,
            "width": row.get("ImageWidth") if isinstance(row.get("ImageWidth"), int) else None,
            "height": row.get("ImageHeight") if isinstance(row.get("ImageHeight"), int) else None, "problem": None}


PURGE_VERSION = "1"
# Photos an Apple Photos library hides (and its own copies) are left out of every list and count.
VISIBLE = "occurrence_id NOT IN (SELECT occurrence_id FROM photo_hidden)"


def purge_private(config) -> list[dict]:
    """Once: take out of the catalog, and out of everything read from it, any file in the private export that an
    earlier version let in (a root chosen at or inside the export, or rows from before the check). Its name and
    headers are then nowhere in Towpath's stores; Space is measured again without it."""
    files = getattr(config, "files", None)
    if files is None or not store_path(config.store_dir, "files").exists():
        return []
    with closing(open_store(config.store_dir, "files", ROLE)) as db:
        row = db.execute("SELECT value FROM photo_progress WHERE key = 'private_purge'").fetchone()
        if row and row[0] == PURGE_VERSION:
            return []
        doomed = [(oid, alias, rel) for oid, alias, rel in db.execute(
            "SELECT occurrence_id, root_alias, rel_path FROM occurrences") if private_in(config, alias, rel)
            and alias in (files.roots or {})]
        for start in range(0, len(doomed), 500):
            chunk = doomed[start:start + 500]
            ids = [d[0] for d in chunk]
            marks = ",".join("?" for _ in ids)
            for table in ("photo_meta", "occurrence_versions", "occurrences"):
                db.execute(f"DELETE FROM {table} WHERE occurrence_id IN ({marks})", ids)
            db.executemany("DELETE FROM content_hashes WHERE root_alias = ? AND rel_path = ?",
                           [(alias, rel) for _, alias, rel in chunk])
        if doomed:  # Space is measured again, without them
            db.execute("DELETE FROM space_summary")
        db.execute("INSERT OR REPLACE INTO photo_progress VALUES ('private_purge', ?, ?)", (PURGE_VERSION, now()))
        db.commit()
    return [{"step": "private-purged", "files": len(doomed)}] if doomed else []


def extract_pending(config, slice_seconds: float = SLICE_SECONDS, clock=time.monotonic,
                    exiftool: str = "exiftool") -> list[dict]:
    """One bounded slice of reading photo headers for the catalog, resuming from its cursor."""
    import shutil

    files = getattr(config, "files", None)
    if files is None or not store_path(config.store_dir, "files").exists() or shutil.which(exiftool) is None:
        return []  # no catalog, or no ExifTool here (it is in the connector image)
    deadline = clock() + slice_seconds
    roots = {alias: Path(r.path) for alias, r in files.roots.items()}
    read = 0
    with closing(open_store(config.store_dir, "files", ROLE)) as db:
        db.execute("UPDATE photo_meta SET taken = NULL WHERE taken IS NOT NULL AND (taken < ? OR taken >= ?)",
                   (f"{FIRST_YEAR:04d}", f"{datetime.now(timezone.utc).year + 2:04d}"))  # read by an earlier version
        row = db.execute("SELECT value FROM photo_progress WHERE key = 'cursor'").fetchone()
        cursor = int(row[0]) if row else 0
        top = db.execute("SELECT max(rowid) FROM occurrences").fetchone()[0] or 0
        while clock() < deadline and cursor < top:
            rows = db.execute("SELECT rowid, occurrence_id, root_alias, rel_path, members, media_type FROM occurrences "
                              "WHERE rowid > ? AND missing_since_run IS NULL ORDER BY rowid LIMIT ?",
                              (cursor, SCAN)).fetchall()
            if not rows:
                cursor = top
                break
            picked, end = [], rows[-1][0]
            for r in rows:
                if r[4] not in ("[]", "", None) or r[2] not in roots or not is_photo(r[3], r[5]) or \
                        is_private(roots[r[2]] / r[3]):
                    continue
                picked.append((r[1], roots[r[2]] / r[3], r[0]))
                if len(picked) >= BATCH:
                    end = r[0]
                    break
            found = read_headers([p for _, p, _ in picked], exiftool)
            for oid, path, _ in picked:
                rec = _record(found.get(str(path)))
                db.execute("INSERT OR REPLACE INTO photo_meta VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                           (oid, rec.get("taken"), rec.get("make"), rec.get("model"), rec.get("lens"), rec.get("lat"),
                            rec.get("lon"), rec.get("width"), rec.get("height"), rec.get("problem"), now()))
            read += len(picked)
            cursor = end
            db.execute("INSERT OR REPLACE INTO photo_progress VALUES ('cursor', ?, ?)", (str(cursor), now()))
            db.commit()
    return [{"step": "photo-metadata", "read": read}] if read else []


def progress(store_dir, role: str = "web") -> dict:
    if not store_path(store_dir, "files").exists():
        return {}
    with closing(open_store(store_dir, "files", role)) as db:
        done = db.execute("SELECT count(*), sum(taken IS NOT NULL), sum(lat IS NOT NULL) FROM photo_meta").fetchone()
        cursor = db.execute("SELECT value FROM photo_progress WHERE key = 'cursor'").fetchone()
        top = db.execute("SELECT max(rowid) FROM occurrences").fetchone()[0] or 0
    return {"read": done[0] or 0, "dated": done[1] or 0, "located": done[2] or 0,
            "share": round(int(cursor[0]) / top * 100) if cursor and top else 0}
