"""Confirming duplicate files by content.

The Space page lists files that share a name and size with another (a hint). This connector job reads those
candidates, largest waste first, and records each file's SHA-256 (read-only, in bounded slices). Groups whose copies
have the same hash are exact duplicates; the page says which are confirmed and which differ. The owner's private
hidden-photo export is never read.
"""

import hashlib
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from towpath.photos import PRIVATE
from towpath.stores import open_store, store_path

ROLE = "connect"
CHUNK = 1024 * 1024
SLICE_SECONDS = 60


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def confirm_pending(config, slice_seconds: float = SLICE_SECONDS, clock=time.monotonic) -> list[dict]:
    from towpath import folders, space

    files = getattr(config, "files", None)
    if files is None or not store_path(config.store_dir, "files").exists():
        return []
    roots = {alias: Path(r.path) for alias, r in files.roots.items()}
    deadline = clock() + slice_seconds
    hashed = 0
    with closing(open_store(config.store_dir, "files", ROLE)) as db:
        found = space.duplicates(db, folders.SID)
        if not found:
            return []
        done = {(r[0], r[1]): r[2] for r in db.execute(
            "SELECT root_alias, rel_path, size FROM content_hashes WHERE provider_id = ?", (folders.SID,))}
        for group in found["top"]:
            for root, rel in group["places"]:
                if clock() >= deadline:
                    break
                if done.get((root, rel)) == group["size"] or root not in roots or PRIVATE.search(rel):
                    continue
                path = roots[root] / rel
                digest, problem = None, None
                try:
                    if path.stat().st_size != group["size"]:
                        problem = "changed since indexing"
                    else:
                        digest = sha256(path)
                except OSError as exc:
                    problem = f"unreadable ({type(exc).__name__})"
                db.execute("INSERT OR REPLACE INTO content_hashes VALUES (?,?,?,?,?,?,?)",
                           (folders.SID, root, rel, group["size"], digest, problem, now()))
                db.commit()
                hashed += 1
    return [{"step": "duplicates-confirmed", "hashed": hashed}] if hashed else []


def verdicts(db, provider_id: str) -> dict[tuple[str, str], str]:
    """Each hashed file's SHA-256 (or None), keyed by (root, path)."""
    return {(r[0], r[1]): r[2] for r in db.execute(
        "SELECT root_alias, rel_path, sha256 FROM content_hashes WHERE provider_id = ?", (provider_id,))}


def judge(places: list, hashes: dict) -> dict:
    """A group's verdict: how many copies share one content, whether all were compared."""
    if any(PRIVATE.search(rel) for _, rel in places):
        return {"state": "not compared (a private folder)", "same": 0}
    seen = [hashes.get((root, rel)) for root, rel in places]
    compared = [h for h in seen if h]
    if len(compared) < 2:
        return {"state": "not compared yet" if len(compared) < len(seen) else "couldn't compare", "same": 0}
    biggest = max(compared.count(h) for h in set(compared))
    state = "same content" if biggest == len(seen) else ("some copies identical" if biggest > 1 else
                                                        "different content")
    return {"state": state if len(compared) == len(seen) else state + " (so far)", "same": biggest}
