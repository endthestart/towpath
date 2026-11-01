"""Fetching the parts that scans asked for."""

import base64
import hashlib

from towpath.adapters import build_connector
from towpath.adapters.errors import NotFound, SyncStop
from towpath.connect.records import ROLE, _new_run, now
from towpath.stores import open_store


def _hashes(data: bytes) -> dict:
    return {"sha256": hashlib.sha256(data).hexdigest(),
            "sha1_b64": base64.b64encode(hashlib.sha1(data).digest()).decode("ascii"),
            "md5": hashlib.md5(data).hexdigest()}


def fetch_requests(config) -> dict:
    """Fulfill content requests from the work queue, one part at a time. Runs only on explicit request.

    A clean stop (quota, budget, authorization) ends the batch early; finished
    fetches are kept and the rest wait for the next run.
    """
    db = open_store(config.store_dir, "source", ROLE)
    queue = open_store(config.store_dir, "queue", ROLE)
    connectors: dict[str, object] = {}
    run_ids: dict[str, str] = {}
    result = {"fetched": 0, "failed": 0, "skipped": 0, "termination": "complete"}
    try:
        done = {r["request_id"] for r in db.execute("SELECT request_id FROM fetches")}
        try:
            for req in queue.execute("SELECT * FROM content_requests ORDER BY seq").fetchall():
                request_id = f"req_{req['seq']:06d}"
                if request_id in done:
                    continue
                item = db.execute("SELECT source_id, native_id, absent_since_run FROM items WHERE item_id = ?",
                                  (req["item_id"],)).fetchone()
                if item is None or item["absent_since_run"]:
                    db.execute("INSERT INTO fetches VALUES (?,?,?,?,?,?)",
                               (request_id, req["item_id"], req["part_id"], "skipped", "item absent", now()))
                    result["skipped"] += 1
                    continue
                source_id = item["source_id"]
                if source_id not in connectors:
                    connectors[source_id] = build_connector(config.sources[source_id], config)
                    run_ids[source_id] = _new_run(db, source_id, "fetch", None)
                try:
                    data = connectors[source_id].fetch(item["native_id"], req["part_id"])
                except NotFound as exc:
                    db.execute("INSERT INTO fetches VALUES (?,?,?,?,?,?)",
                               (request_id, req["item_id"], req["part_id"], "failed",
                                getattr(exc, "detail", "not found"), now()))
                    result["failed"] += 1
                    continue
                h = _hashes(data)
                db.execute("INSERT OR IGNORE INTO cache (sha256, bytes, cached_at) VALUES (?, ?, ?)",
                           (h["sha256"], data, now()))
                db.execute("UPDATE parts SET sha256=?, sha1_b64=?, md5=?, fetched_run=? WHERE item_id=? AND part_id=?",
                           (h["sha256"], h["sha1_b64"], h["md5"], run_ids[source_id], req["item_id"], req["part_id"]))
                db.execute("INSERT INTO fetches VALUES (?,?,?,?,?,?)",
                           (request_id, req["item_id"], req["part_id"], "fetched", h["sha256"], now()))
                result["fetched"] += 1
                db.commit()
            result.update(_presence_lookups(config, db, queue, connectors, run_ids))
        except SyncStop as exc:
            result.update(termination=exc.code, reason=exc.reason, exit_code=exc.exit_code)
        for run_id in run_ids.values():
            db.execute("UPDATE runs SET finished_at = ?, complete = ?, termination = ?, reason = ? WHERE run_id = ?",
                       (now(), int(result["termination"] == "complete"), result["termination"],
                        result.get("reason"), run_id))
        db.commit()
    finally:
        db.close()
        queue.close()
        for connector in connectors.values():
            if hasattr(connector, "close"):
                connector.close()
    return result


def _presence_lookups(config, db, queue, connectors, run_ids) -> dict:
    """Ask lookup destinations whether they already hold requested checksums.

    Results are observations at a run, not guarantees: a delivery must check again.
    """
    answered = {(r["source_id"], r["algorithm"], r["checksum"]) for r in db.execute(
        "SELECT source_id, algorithm, checksum FROM destination_lookups")}
    pending: dict[tuple[str, str], list[str]] = {}
    for req in queue.execute("SELECT * FROM presence_requests ORDER BY seq"):
        key = (req["destination_id"], req["algorithm"], req["checksum"])
        if key not in answered:
            pending.setdefault((req["destination_id"], req["algorithm"]), []).append(req["checksum"])
    counts = {"presence_checked": 0}
    for (dest_id, algorithm), checksums in pending.items():
        if dest_id not in connectors:
            connectors[dest_id] = build_connector(config.sources[dest_id], config)
            run_ids[dest_id] = _new_run(db, dest_id, "lookup", None)
        for checksum, (present, remote_id) in connectors[dest_id].lookup(algorithm, checksums).items():
            db.execute("""INSERT OR REPLACE INTO destination_lookups (source_id, algorithm, checksum, present,
                          remote_id, checked_run, checked_at) VALUES (?,?,?,?,?,?,?)""",
                       (dest_id, algorithm, checksum, int(present), remote_id, run_ids[dest_id], now()))
            counts["presence_checked"] += 1
        db.commit()
    return counts
