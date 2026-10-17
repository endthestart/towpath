"""Photo previews for the owner's pages: each photo's own small embedded camera thumbnail.

ExifTool reads the thumbnail a camera or phone stores inside the photo (``-b -ThumbnailImage``; a small embedded
preview otherwise), headers only, never writing. No preview is made, and none is shown, for:

- the owner's private photo export (``towpath.photos.private_in``, on the full path);
- any file of an Apple Photos library that isn't an original the library shows (``towpath.apple_photos``: hidden or
  deleted photos, and the library's own copies), nor for any file of a library whose database hasn't been read yet;
- anything at all while previews are turned off (turning them off also deletes those already made).

Previews go only to the owner's own browser, from the web service. This module never calls a model, and no model
task reads photos or previews.
"""

import base64
import json
import os
import subprocess
import tempfile
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from towpath.stores import open_store, store_path

ROLE = "connect"
TARGET, KEY = "towpath:photos", "previews"
BATCH = 200
SLICE_SECONDS = 60
TIMEOUT = 300
MAX_BYTES = 300_000  # an embedded preview larger than this isn't kept


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def enabled(store_dir, role: str = ROLE) -> bool:
    """On unless the owner turned previews off."""
    if not store_path(store_dir, "decisions").exists():
        return True
    with closing(open_store(store_dir, "decisions", role)) as db:
        row = db.execute("SELECT value FROM settings WHERE target_id = ? AND key = ?", (TARGET, KEY)).fetchone()
    return json.loads(row[0]) != "off" if row else True


def set_enabled(store_dir, on: bool, author: str = "owner") -> None:
    value = json.dumps("on" if on else "off")
    with closing(open_store(store_dir, "decisions", "web")) as db:
        db.execute("INSERT OR REPLACE INTO settings (target_id, key, value, author, at) VALUES (?,?,?,?,?)",
                   (TARGET, KEY, value, author, now()))
        db.execute("INSERT INTO decision_log (kind, target_id, detail, author, at) VALUES ('photo-previews', ?, ?, ?, "
                   "?)", (TARGET, value, author, now()))
        db.commit()


def read_thumbnails(paths: list[Path], exiftool: str = "exiftool") -> dict[str, dict]:
    """Each file's embedded thumbnail (or small preview) and orientation, keyed by path."""
    if not paths:
        return {}
    with tempfile.NamedTemporaryFile("w", suffix=".args", delete=False) as args:
        args.write("\n".join(str(p) for p in paths))
    try:
        proc = subprocess.run([exiftool, "-json", "-b", "-n", "-fast2", "-q", "-q", "-charset", "filename=utf8",
                               "-ThumbnailImage", "-PreviewImage", "-Orientation", "-@", args.name],
                              capture_output=True, timeout=TIMEOUT, check=False)
    finally:
        os.unlink(args.name)
    try:
        rows = json.loads(proc.stdout.decode("utf-8", "replace") or "[]")
    except ValueError:
        return {}
    out = {}
    for r in rows:
        image = None
        for key in ("ThumbnailImage", "PreviewImage"):
            value = r.get(key)
            if isinstance(value, str) and value.startswith("base64:"):
                data = base64.b64decode(value[len("base64:"):])
                if data[:2] == b"\xff\xd8" and len(data) <= MAX_BYTES:  # a JPEG, and small
                    image = data
                    break
        orientation = r.get("Orientation") if isinstance(r.get("Orientation"), int) else None
        out[r.get("SourceFile")] = {"jpeg": image, "orientation": orientation}
    return out


def _forbidden(config, alias: str, rel: str, checked: set) -> str | None:
    """Why no preview may be made for this file (besides being hidden in Apple Photos), or None."""
    from towpath.apple_photos import library_of
    from towpath.photos import private_in

    if private_in(config, alias, rel):
        return "private"
    lib = library_of(rel)
    if lib is not None and (alias, lib) not in checked:
        return "library not checked yet"
    return None


def thumbs_pending(config, slice_seconds: float = SLICE_SECONDS, clock=time.monotonic,
                   exiftool: str = "exiftool") -> list[dict]:
    """One bounded slice: drop previews that may no longer be kept, then make the next ones."""
    import shutil

    from towpath.apple_photos import checked_libraries

    files = getattr(config, "files", None)
    if files is None or not store_path(config.store_dir, "files").exists():
        return []
    if not enabled(config.store_dir):
        if store_path(config.store_dir, "thumbs").exists():
            with closing(open_store(config.store_dir, "thumbs", ROLE)) as t:
                gone = t.execute("DELETE FROM thumbs").rowcount
                t.execute("DELETE FROM thumb_progress")
                t.commit()
            return [{"step": "photo-previews-deleted", "previews": gone}] if gone else []
        return []
    roots = {alias: Path(r.path) for alias, r in files.roots.items()}
    deadline = clock() + slice_seconds
    made = dropped = 0
    with closing(open_store(config.store_dir, "files", ROLE)) as db, \
            closing(open_store(config.store_dir, "thumbs", ROLE)) as t:
        hidden = {r[0] for r in db.execute("SELECT occurrence_id FROM photo_hidden")}
        checked = checked_libraries(db)
        version = (db.execute("SELECT value FROM photo_progress WHERE key = 'hidden_version'").fetchone() or ["0"])[0]
        seen = (t.execute("SELECT value FROM thumb_progress WHERE key = 'hidden_version'").fetchone() or [None])[0]
        if seen != version:  # something was hidden since: take its preview away
            for (oid,) in t.execute("SELECT occurrence_id FROM thumbs WHERE jpeg IS NOT NULL").fetchall():
                if oid in hidden:
                    t.execute("DELETE FROM thumbs WHERE occurrence_id = ?", (oid,))
                    dropped += 1
            t.execute("INSERT OR REPLACE INTO thumb_progress VALUES ('hidden_version', ?, ?)", (version, now()))
            t.commit()
        if shutil.which(exiftool) is None:
            return [{"step": "photo-previews", "made": 0, "dropped": dropped}] if dropped else []
        cursor = int((t.execute("SELECT value FROM thumb_progress WHERE key = 'cursor'").fetchone() or ["0"])[0])
        while clock() < deadline:
            rows = db.execute("SELECT p.rowid, p.occurrence_id, o.root_alias, o.rel_path FROM photo_meta p JOIN "
                              "occurrences o USING (occurrence_id) WHERE p.rowid > ? AND p.problem IS NULL AND "
                              "o.missing_since_run IS NULL ORDER BY p.rowid LIMIT ?", (cursor, BATCH)).fetchall()
            if not rows:
                break
            wanted, waiting = [], False
            for rowid, oid, alias, rel in rows:
                why = "hidden" if oid in hidden else _forbidden(config, alias, rel, checked)
                if why == "library not checked yet":
                    waiting = True  # wait at this file until its library's database has been read
                    break
                if why:
                    t.execute("INSERT OR REPLACE INTO thumbs VALUES (?, NULL, NULL, ?, ?)", (oid, why, now()))
                else:
                    wanted.append((oid, roots[alias] / rel))
                cursor = rowid
            found = read_thumbnails([p for _, p in wanted], exiftool)
            for oid, path in wanted:
                got = found.get(str(path)) or {}
                t.execute("INSERT OR REPLACE INTO thumbs VALUES (?,?,?,?,?)",
                          (oid, got.get("jpeg"), got.get("orientation"),
                           None if got.get("jpeg") else "no embedded preview", now()))
                made += bool(got.get("jpeg"))
            t.execute("INSERT OR REPLACE INTO thumb_progress VALUES ('cursor', ?, ?)", (str(cursor), now()))
            t.commit()
            if waiting:
                break
    return [{"step": "photo-previews", "made": made, "dropped": dropped}] if made or dropped else []


def preview(store_dir, occurrence_id: str, role: str = "web") -> dict | None:
    """A preview for the owner's page, checked again here: never for a hidden or private photo, never while off."""
    from towpath.photos import is_private

    if not enabled(store_dir, role) or not store_path(store_dir, "thumbs").exists():
        return None
    with closing(open_store(store_dir, "files", role)) as db:
        row = db.execute("SELECT o.rel_path, h.occurrence_id IS NOT NULL FROM occurrences o LEFT JOIN photo_hidden h "
                         "USING (occurrence_id) WHERE o.occurrence_id = ?", (occurrence_id,)).fetchone()
    if row is None or row[1] or is_private(row[0]):
        return None
    with closing(open_store(store_dir, "thumbs", role)) as t:
        found = t.execute("SELECT jpeg, orientation FROM thumbs WHERE occurrence_id = ?", (occurrence_id,)).fetchone()
    return {"jpeg": found[0], "orientation": found[1]} if found and found[0] else None


def available(store_dir, occurrence_ids: list[str], role: str = "web") -> dict[str, int | None]:
    """Of these photos, those with a preview, and each one's orientation."""
    if not occurrence_ids or not enabled(store_dir, role) or not store_path(store_dir, "thumbs").exists():
        return {}
    with closing(open_store(store_dir, "thumbs", role)) as t:
        marks = ",".join("?" for _ in occurrence_ids)
        return {r[0]: r[1] for r in t.execute(f"SELECT occurrence_id, orientation FROM thumbs WHERE jpeg IS NOT NULL "
                                              f"AND occurrence_id IN ({marks})", occurrence_ids)}
