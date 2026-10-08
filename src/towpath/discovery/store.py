"""files.db: references, versions, coverage, citations, and recoveries (connect role).

Rebuildable. Occurrence IDs derive from locations, so a rebuilt catalog gets
the same IDs. Only a complete import run marks occurrences missing; a partial,
failed, or interrupted run records what it saw and establishes no absence.
"""

import json
import uuid
from datetime import datetime, timezone

from towpath.canonical import canonical_json, short_id
from towpath.discovery.records import Occurrence
from towpath.stores import CATALOG_COUNTS, open_store

WRITER = "connect"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect_rw(config):
    return open_store(config.store_dir, "files", WRITER)


def connect_ro(config):
    return open_store(config.store_dir, "files", "worker")


def begin_run(db, provider_id: str, kind: str) -> str:
    run_id = f"frun_{uuid.uuid4().hex[:16]}"
    db.execute("INSERT INTO runs (run_id, provider_id, kind, started_at) VALUES (?,?,?,?)",
               (run_id, provider_id, kind, now()))
    db.commit()
    return run_id


def finish_run(db, run_id: str, termination: str, items_seen: int, reason: str | None = None) -> None:
    db.execute("UPDATE runs SET finished_at = ?, termination = ?, reason = ?, items_seen = ? WHERE run_id = ?",
               (now(), termination, reason, items_seen, run_id))
    db.commit()


def record_coverage(db, run_id: str, provider_id: str, root: str | None, complete: bool, scanned: int,
                    by_status: dict, reason: str | None = None) -> None:
    db.execute("INSERT OR REPLACE INTO coverage (run_id, provider_id, root_alias, complete, scanned, by_status, "
               "reason) VALUES (?,?,?,?,?,?,?)",
               (run_id, provider_id, root, int(complete), scanned, canonical_json(by_status), reason))
    db.commit()


def observe(db, occ: Occurrence, run_id: str) -> str:
    """Upsert one occurrence; returns 'new', 'same', or 'changed' (version differs from the last seen)."""
    loc = occ.locator
    data = occ.to_dict()
    row = db.execute("SELECT version FROM occurrences WHERE occurrence_id = ?", (occ.occurrence_id,)).fetchone()
    values = (loc.native_id, canonical_json(data["locator"]["members"]), occ.media_type, occ.size,
              canonical_json(data["dates"]), canonical_json(occ.hashes), canonical_json(data["extraction"]),
              occ.version)
    if row is None:
        db.execute("INSERT INTO occurrences (occurrence_id, provider_id, native_id, root_alias, rel_path, members, "
                   "media_type, size, dates, hashes, extraction, version, first_seen_run, last_seen_run, updated_at) "
                   "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (occ.occurrence_id, loc.provider_id, *values[:1], loc.root, loc.path, *values[1:], run_id,
                    run_id, now()))
        state = "new"
    else:
        state = "same" if row["version"] == occ.version else "changed"
        db.execute("UPDATE occurrences SET native_id = ?, members = ?, media_type = ?, size = ?, dates = ?, "
                   "hashes = ?, extraction = ?, version = ?, last_seen_run = ?, missing_since_run = NULL, "
                   "updated_at = ? WHERE occurrence_id = ?", (*values, run_id, now(), occ.occurrence_id))
    if occ.version is not None:
        db.execute("INSERT OR IGNORE INTO occurrence_versions (occurrence_id, version, first_seen_run) VALUES (?,?,?)",
                   (occ.occurrence_id, occ.version, run_id))
    return state


def refresh_counts(db, provider_id: str, root: str) -> None:
    """Recount one root's present occurrences by extraction status (see ``catalog_counts``)."""
    rows = db.execute(f"{CATALOG_COUNTS} AND provider_id = ? AND root_alias = ? GROUP BY status ORDER BY status",
                      (provider_id, root)).fetchall()
    db.execute("INSERT OR REPLACE INTO catalog_counts (provider_id, root_alias, counts, updated_at) VALUES (?,?,?,?)",
               (provider_id, root, canonical_json([[r["status"], r["n"]] for r in rows]), now()))
    db.commit()


def counts(db, provider_id: str, root: str) -> list:
    """[[status, count], ...] for one root: kept by ``refresh_counts``, or counted now for a root never imported."""
    row = db.execute("SELECT counts FROM catalog_counts WHERE provider_id = ? AND root_alias = ?",
                     (provider_id, root)).fetchone()
    if row is not None:
        return json.loads(row["counts"])
    return [[r["status"], r["n"]] for r in db.execute(f"{CATALOG_COUNTS} AND provider_id = ? AND root_alias = ? "
                                                      "GROUP BY status", (provider_id, root))]


def mark_missing(db, provider_id: str, root: str, run_id: str) -> int:
    """After a complete import of ``root``: occurrences it did not see become missing (never deleted)."""
    cur = db.execute("UPDATE occurrences SET missing_since_run = ? WHERE provider_id = ? AND root_alias = ? "
                     "AND last_seen_run != ? AND missing_since_run IS NULL", (run_id, provider_id, root, run_id))
    return cur.rowcount


def get(db, occurrence_id: str):
    return db.execute("SELECT * FROM occurrences WHERE occurrence_id = ?", (occurrence_id,)).fetchone()


def row_to_dict(row) -> dict:
    data = dict(row)
    for key in ("members", "dates", "hashes", "extraction"):
        data[key] = json.loads(data[key])
    return data


def citation_id(occurrence_id: str, version: str | None, source: dict | None, location: dict) -> str:
    return short_id("cit", occurrence_id, version or "", canonical_json(source), canonical_json(location))


def add_citation(db, occurrence_id: str, version: str | None, source: dict | None, location: dict,
                 excerpt_sha256: str) -> dict:
    """Pin a cited location to the provider's version *and* the source file's stamp at citation time."""
    cid = citation_id(occurrence_id, version, source, location)
    db.execute("INSERT OR IGNORE INTO citations (citation_id, occurrence_id, version, source_stamp, location, "
               "excerpt_sha256, created_at) VALUES (?,?,?,?,?,?,?)",
               (cid, occurrence_id, version, canonical_json(source), canonical_json(location), excerpt_sha256,
                now()))
    db.commit()
    return {"citation_id": cid, "occurrence_id": occurrence_id, "version": version, "source": source,
            "location": location, "excerpt_sha256": excerpt_sha256}


def get_citation(db, cid: str) -> dict | None:
    row = db.execute("SELECT * FROM citations WHERE citation_id = ?", (cid,)).fetchone()
    if row is None:
        return None
    data = dict(row)
    data["location"] = json.loads(data["location"])
    data["source"] = json.loads(data.pop("source_stamp") or "null")
    return data


def add_recovery(db, occurrence_id: str, version: str | None, path: str, sha256: str, size: int) -> str:
    rid = short_id("rec", occurrence_id, version or "", sha256)
    db.execute("INSERT OR REPLACE INTO recoveries (recovery_id, occurrence_id, version, path, sha256, size, "
               "created_at) VALUES (?,?,?,?,?,?,?)", (rid, occurrence_id, version, path, sha256, size, now()))
    db.commit()
    return rid
