"""Keeping measurements current with the owner's marks, and finding likely duplicates."""

import json

from towpath.canonical import digest
from towpath.space.measuring import (
    CONTAINER_KINDS,
    FOLDER_SUFFIXES,
    KEEP_KEY,
    KEEP_VALUE,
    MODIFIED_MONTH,
    VERSION,
    measure,
    _now,
    _parent,
)


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
