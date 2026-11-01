"""Explicit upgrades of existing stores."""

import sqlite3
from pathlib import Path

from towpath.stores.opening import _add_columns, open_store, store_path
from towpath.stores.schemas import SCHEMAS, WRITERS

UPGRADE_COMMAND = "towpath stores upgrade --store-dir <folder holding the .db files>"


class SchemaOutdated(RuntimeError):
    """An existing store predates tables or columns this version reads. Readers never migrate."""

    def __init__(self, reports: list[dict]):
        self.reports = reports
        names = ", ".join(f"{r['store']} ({', '.join(r['missing_tables'] + sorted(r['missing_columns']))})"
                          for r in reports)
        super().__init__(f"these stores need an upgrade before use: {names}. Run: {UPGRADE_COMMAND}")


def writer_role(store: str) -> str:
    """The role an upgrade opens ``store`` with: one of its own writers, chosen deterministically."""
    return sorted(WRITERS[store])[0]


def _expected(store: str) -> dict[str, set[str]]:
    reference = sqlite3.connect(":memory:")
    try:
        reference.executescript(SCHEMAS[store])
        _add_columns(reference, store)
        return _shape(reference)
    finally:
        reference.close()


def _shape(conn: sqlite3.Connection) -> dict[str, set[str]]:
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' "
                                         "AND name NOT LIKE 'sqlite_%'")]
    return {t: {row[1] for row in conn.execute(f"PRAGMA table_info({t})")} for t in tables}


def schema_status(store_dir: Path, names=None) -> list[dict]:
    """Read-only check of every store (or ``names``): which tables and columns it lacks. Writes nothing."""
    reports = []
    for store in names or SCHEMAS:
        if store not in SCHEMAS:
            raise ValueError(f"unknown store {store!r}")
        path = store_path(store_dir, store)
        report = {"store": store, "path": str(path), "exists": path.exists(), "writer_role": writer_role(store),
                  "missing_tables": [], "missing_columns": {}}
        if path.exists():
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            try:
                actual = _shape(conn)
            finally:
                conn.close()
            for table, columns in _expected(store).items():
                if table not in actual:
                    report["missing_tables"].append(table)
                elif columns - actual[table]:
                    report["missing_columns"][table] = sorted(columns - actual[table])
            report["missing_tables"].sort()
        report["current"] = not report["missing_tables"] and not report["missing_columns"]
        reports.append(report)
    return reports


def require_current(store_dir: Path, names) -> None:
    """Raise SchemaOutdated if any existing store in ``names`` lacks what this version reads."""
    outdated = [r for r in schema_status(store_dir, names) if not r["current"]]
    if outdated:
        raise SchemaOutdated(outdated)


def upgrade(store_dir: Path, names=None) -> list[dict]:
    """Bring existing stores up to date, each opened by its own writer role. Idempotent.

    Only ``CREATE TABLE IF NOT EXISTS`` and ``ALTER TABLE ... ADD COLUMN`` run, so no existing row changes.
    Stores that do not exist yet are left alone: their writer creates them when it first runs.
    """
    reports = []
    for before in schema_status(store_dir, names):
        report = {"store": before["store"], "writer_role": before["writer_role"], "exists": before["exists"],
                  "added_tables": [], "added_columns": {}}
        if before["exists"] and not before["current"]:
            open_store(store_dir, before["store"], before["writer_role"]).close()
            after = schema_status(store_dir, [before["store"]])[0]
            report["added_tables"] = sorted(set(before["missing_tables"]) - set(after["missing_tables"]))
            report["added_columns"] = {t: c for t, c in before["missing_columns"].items()
                                       if t not in after["missing_columns"]}
            before = after
        report["current"] = before["current"]
        reports.append(report)
    return reports
