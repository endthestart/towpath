"""Reading measurements for the web service."""

import json


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
