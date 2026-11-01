"""What the unified adapters share: limits, paging through a store, and building search expressions."""

import json

from towpath.unified.federation import StaleReference

READ_ROLE = "web"  # a reader of the source and files stores: opened read-only (mode=ro)
SCAN_BATCH = 200
MAX_SCAN = 5000
MAX_TEXT_IDS = 500  # messages matched by text that a catalog page considers, newest first
FILE_DEPTH = {"recoll": "content-index", "fixture": "content-index", "manifest": "catalog", "sist2": None}


def _scan(fetch, accept, limit: int, offset: int) -> tuple[list, int | None, bool]:
    """Page through raw rows, keeping those ``accept`` turns into results.

    Returns (results, next raw offset or None, scan bound reached). The cursor is a raw-row offset, so
    filtering never makes a capped scan look finished.
    """
    results, position, bounded = [], offset, False
    while True:
        rows = fetch(position, SCAN_BATCH)
        for row in rows:
            position += 1
            result = accept(row)
            if result is None:
                continue
            if len(results) == limit:
                return results, position - 1, False
            results.append(result)
        if len(rows) < SCAN_BATCH:
            return results, None, False
        if position - offset >= MAX_SCAN:
            bounded = True
            return results, position, bounded


def _like(word: str) -> str:
    return "%" + word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _expansion_cursor(cursor: str | None) -> dict:
    if cursor is None:
        return {"page": None, "after": None}
    try:
        state = json.loads(cursor)
    except ValueError:
        state = None
    if not isinstance(state, dict) or set(state) != {"page", "after"}:
        raise StaleReference("this cursor does not belong to this source")
    return state


def _fts_query(words, extensions) -> str | None:
    """A trigram-index query for the files catalog: rows holding every word and, when extensions are asked for,
    one of them. A quoted phrase matches as a substring, case-insensitively. Terms under three characters can't
    be looked up in a trigram index; they are left to the exact match, and None means nothing narrows."""
    def phrase(term: str) -> str:
        return '"' + term.replace('"', '""') + '"'

    parts = [phrase(w) for w in words if len(w) >= 3]
    suffixes = [f".{e}" for e in extensions]
    if suffixes and all(len(x) >= 3 for x in suffixes):
        parts.append("(" + " OR ".join(phrase(x) for x in suffixes) + ")")
    return " AND ".join(parts) or None


def _any(clause: str, n: int) -> str:
    return "(" + " OR ".join([clause] * n) + ")"


def _offset(cursor: str | None) -> int:
    try:
        value = int(cursor or 0)
    except ValueError:
        raise StaleReference("this cursor does not belong to this source") from None
    if value < 0:
        raise StaleReference("this cursor does not belong to this source")
    return value


def _labels(db, item_id: str) -> list | None:
    row = db.execute("SELECT o.labels FROM observations o JOIN runs r ON r.run_id = o.run_id "
                     "WHERE o.item_id = ? ORDER BY r.seq DESC LIMIT 1",
                     (item_id,)).fetchone()
    return json.loads(row["labels"]) if row else None
