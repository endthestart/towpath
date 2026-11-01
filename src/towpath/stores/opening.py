"""Opening a store for a role: read-write for its writers, read-only otherwise."""

import sqlite3
from pathlib import Path

from towpath.stores.schemas import ADDED_COLUMNS, SCHEMAS, WRITERS


class RoleError(PermissionError):
    pass


def store_path(store_dir: Path, store: str) -> Path:
    return Path(store_dir) / f"{store}.db"


def open_store(store_dir: Path, store: str, role: str) -> sqlite3.Connection:
    """Open ``store`` for ``role``: read-write for its writers, read-only otherwise.

    A reader whose store does not exist yet gets an empty in-memory copy of the
    schema, so it never creates another role's file.
    """
    if store not in SCHEMAS:
        raise ValueError(f"unknown store {store!r}")
    path = store_path(store_dir, store)
    if role in WRITERS[store]:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path)
        conn.executescript(SCHEMAS[store])
        _add_columns(conn, store)
    elif path.exists():
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    else:
        conn = sqlite3.connect(":memory:")
        conn.executescript(SCHEMAS[store])
        _add_columns(conn, store)
        conn.execute("PRAGMA query_only = ON")
    conn.row_factory = sqlite3.Row
    return conn


CATALOG_COUNTS = ("SELECT provider_id, root_alias, json_extract(extraction, '$.status') AS status, count(*) AS n "
                  "FROM occurrences WHERE missing_since_run IS NULL")


def _add_columns(conn: sqlite3.Connection, store: str) -> None:
    for table, columns in ADDED_COLUMNS.get(store, {}).items():
        present = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        for name, kind in columns.items():
            if name not in present:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")
    if store == "files":
        _fill_catalog_tables(conn)
    conn.commit()


def _empty(conn: sqlite3.Connection, table: str) -> bool:
    return not conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()


def _fill_catalog_tables(conn: sqlite3.Connection) -> None:
    """A catalog written before the search index or the counts existed: index and count what it holds, once. Another
    writer may commit after a first look, so the look that decides is repeated holding the write lock."""
    if not (_empty(conn, "occurrences_text") or _empty(conn, "catalog_counts")):
        return
    conn.execute("BEGIN IMMEDIATE")
    if _empty(conn, "occurrences_text"):
        conn.execute("INSERT INTO occurrences_text (rowid, text) SELECT rowid, rel_path || ' ' || members "
                     "FROM occurrences")
    if _empty(conn, "catalog_counts"):
        conn.execute(f"INSERT INTO catalog_counts (provider_id, root_alias, counts, updated_at) "
                     f"SELECT provider_id, root_alias, json_group_array(json_array(status, n)), "
                     f"strftime('%Y-%m-%dT%H:%M:%SZ', 'now') FROM ({CATALOG_COUNTS} GROUP BY provider_id, root_alias, "
                     f"status) GROUP BY provider_id, root_alias")


def require_writer(store: str, role: str) -> None:
    if role not in WRITERS[store]:
        raise RoleError(f"role {role!r} may not write the {store} store")
