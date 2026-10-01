"""The ``towpath-worker`` role: scans, presence checks, and delivery proposals.

It reads the source index and decisions read-only, appends content requests
to the work queue, and writes only the derived store. Proposals produced here
have no execution path in this slice.
"""

import json
from datetime import datetime, timezone

from towpath.canonical import digest, short_id
from towpath.stores import open_store

ROLE = "worker"
CHECKSUM_COLUMNS = {"sha256": "sha256", "sha1-base64": "sha1_b64", "md5": "md5"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def match_key(selector_id: str, source_id: str, native_id: str, part_id: str, destination_id: str) -> str:
    return short_id("mk", selector_id, source_id, native_id, part_id, destination_id, length=24)


def _settings(decisions) -> dict[tuple[str, str], str]:
    return {(r["target_id"], r["key"]): r["value"] for r in decisions.execute("SELECT * FROM settings")}


def _latest_labels(source, iid: str) -> list[str]:
    row = source.execute("SELECT labels FROM observations WHERE item_id = ? ORDER BY run_id DESC LIMIT 1",
                         (iid,)).fetchone()
    return json.loads(row["labels"]) if row else []


def _candidates(source, selector) -> list:
    placeholders = ",".join("?" for _ in selector.media_types)
    sql = f"""SELECT p.*, i.source_id, i.native_id, i.absent_since_run FROM parts p
              JOIN items i USING (item_id) JOIN sources s ON s.source_id = i.source_id
              WHERE s.kind = 'mail-provider' AND p.mime_type IN ({placeholders}) AND p.size >= ?"""
    args: list = [*selector.media_types, selector.min_bytes]
    if selector.max_bytes is not None:
        sql += " AND p.size <= ?"
        args.append(selector.max_bytes)
    if selector.disposition:
        sql += " AND p.disposition = ?"
        args.append(selector.disposition)
    rows = source.execute(sql + " ORDER BY i.native_id DESC, p.part_id", args).fetchall()
    return [r for r in rows if not set(_latest_labels(source, r["item_id"])) & set(selector.exclude_labels)]


def run_scan(config, selector_id: str) -> dict:
    selector = config.selectors[selector_id]
    source = open_store(config.store_dir, "source", ROLE)
    decisions = open_store(config.store_dir, "decisions", ROLE)
    queue = open_store(config.store_dir, "queue", ROLE)
    derived = open_store(config.store_dir, "derived", ROLE)

    dest = source.execute("SELECT descriptor FROM sources WHERE source_id = ?", (selector.destination,)).fetchone()
    if dest is None:
        raise RuntimeError(f"destination {selector.destination} has not been synced yet")
    algorithm = json.loads(dest["descriptor"])["algorithm"]
    held = {r["checksum"] for r in source.execute(
        "SELECT checksum FROM destination_entries WHERE source_id = ? AND algorithm = ? AND absent_since_run IS NULL",
        (selector.destination, algorithm))}
    dismissed = {r["match_key"] for r in decisions.execute("SELECT match_key FROM dismissals")}
    settings = _settings(decisions)
    counts = {"candidates": 0, "waiting": 0, "present": 0, "proposed": 0, "dismissed": 0, "stale": 0}

    for c in _candidates(source, selector):
        key = match_key(selector.id, c["source_id"], c["native_id"], c["part_id"], selector.destination)
        if c["absent_since_run"]:
            n = derived.execute("UPDATE proposals SET state = 'stale', updated_at = ? WHERE match_key = ? "
                                "AND state = 'proposed'", (now(), key)).rowcount
            counts["stale"] += n
            continue
        counts["candidates"] += 1
        if key in dismissed:
            status = "dismissed"
            derived.execute("UPDATE proposals SET state = 'dismissed', updated_at = ? WHERE match_key = ?",
                            (now(), key))
            counts["dismissed"] += 1
        else:
            if selector.classifier and settings.get((c["item_id"], "model_use")) != "excluded":
                derived.execute("""INSERT OR IGNORE INTO model_input_queue (item_id, part_id, task, status)
                                   VALUES (?, ?, ?, 'awaiting-endpoint')""",
                                (c["item_id"], c["part_id"], selector.classifier))
            checksum = c[CHECKSUM_COLUMNS[algorithm]]
            if c["sha256"] is None:
                queue.execute("""INSERT OR IGNORE INTO content_requests (item_id, part_id, requested_by, priority,
                                 created_at) VALUES (?, ?, ?, 'background', ?)""",
                              (c["item_id"], c["part_id"], f"scan:{selector.id}", now()))
                status = "waiting"
                counts["waiting"] += 1
            elif checksum in held:
                status = "present"
                derived.execute("UPDATE proposals SET state = 'resolved-present', updated_at = ? "
                                "WHERE match_key = ? AND state = 'proposed'", (now(), key))
                counts["present"] += 1
            else:
                status = "absent"
                body = {
                    "schema": "towpath.delivery.proposal/0",
                    "action": "deliver",
                    "selector_id": selector.id,
                    "destination_id": selector.destination,
                    "source_id": c["source_id"],
                    "item_id": c["item_id"],
                    "native_id": c["native_id"],
                    "part_id": c["part_id"],
                    "file_name": c["filename"],
                    "media_type": c["mime_type"],
                    "bytes": c["size"],
                    "sha256": c["sha256"],
                    "destination_checksum": {"algorithm": algorithm, "value": checksum},
                    "precondition": {"item_present": True, "part_sha256": c["sha256"]},
                }
                d = digest(body)
                proposal_id = f"dprop_{d[:16]}"
                existing = derived.execute("SELECT state FROM proposals WHERE proposal_id = ?",
                                           (proposal_id,)).fetchone()
                if existing is None:
                    derived.execute("""INSERT INTO proposals (proposal_id, match_key, digest, body, state, updated_at)
                                       VALUES (?, ?, ?, ?, 'proposed', ?)""",
                                    (proposal_id, key, d, json.dumps(body, sort_keys=True), now()))
                counts["proposed"] += 1
        derived.execute("""INSERT INTO scan_matches (match_key, selector_id, item_id, part_id, destination_id, status,
                           dest_algorithm, dest_checksum, updated_at) VALUES (?,?,?,?,?,?,?,?,?)
                           ON CONFLICT (match_key) DO UPDATE SET status = excluded.status,
                           dest_checksum = excluded.dest_checksum, updated_at = excluded.updated_at""",
                        (key, selector.id, c["item_id"], c["part_id"], selector.destination, status, algorithm,
                         c[CHECKSUM_COLUMNS[algorithm]], now()))

    derived.execute("""INSERT INTO scans (selector_id, at, candidates, waiting, present, proposed, dismissed)
                       VALUES (?,?,?,?,?,?,?)""",
                    (selector.id, now(), counts["candidates"], counts["waiting"], counts["present"],
                     counts["proposed"], counts["dismissed"]))
    for conn in (derived, queue):
        conn.commit()
        conn.close()
    source.close()
    decisions.close()
    return {"selector_id": selector.id, "destination_id": selector.destination, "algorithm": algorithm, **counts}


def list_proposals(config, state: str | None = None) -> list[dict]:
    derived = open_store(config.store_dir, "derived", "web")
    sql = "SELECT * FROM proposals" + (" WHERE state = ?" if state else "") + " ORDER BY proposal_id"
    rows = [dict(r) for r in derived.execute(sql, (state,) if state else ())]
    derived.close()
    for r in rows:
        r["body"] = json.loads(r["body"])
    return rows


def model_queue(config) -> list[dict]:
    derived = open_store(config.store_dir, "derived", "web")
    rows = [dict(r) for r in derived.execute("SELECT * FROM model_input_queue ORDER BY item_id, part_id")]
    derived.close()
    return rows
