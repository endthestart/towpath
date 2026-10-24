"""The model-input queue: metadata tasks the scan queued for a model, run when the owner asks."""

import json

from towpath.models.gateway import infer, now
from towpath.models.profiles import fingerprint
from towpath.stores import open_store

ROLE = "worker"

STATEMENT_SCHEMA = {"type": "object",
                    "properties": {"is_statement": {"type": "boolean"},
                                   "confidence": {"type": "number", "minimum": 0, "maximum": 1}},
                    "required": ["is_statement", "confidence"], "additionalProperties": False}
TASKS = {"doc.is_statement": ("Decide whether this email attachment is a financial or account statement. "
                              "Use only the metadata below.", STATEMENT_SCHEMA)}


def run_queue(config) -> dict:
    """Process the model-input queue. A changed endpoint profile fails queued work; it never reroutes."""
    derived = open_store(config.store_dir, "derived", ROLE)
    source = open_store(config.store_dir, "source", ROLE)
    counts: dict[str, int] = {}
    rows = derived.execute("SELECT * FROM model_input_queue WHERE status IN ('awaiting-endpoint', 'queued')").fetchall()
    for row in rows:
        task = row["task"]
        endpoint_id = config.tasks.get(task)
        if endpoint_id is None or task not in TASKS:
            counts["disabled"] = counts.get("disabled", 0) + 1
            continue
        fp = fingerprint(config.endpoints[endpoint_id])
        if row["endpoint_fingerprint"] is None:
            derived.execute("UPDATE model_input_queue SET endpoint_fingerprint = ?, status = 'queued' "
                            "WHERE item_id = ? AND part_id = ? AND task = ?",
                            (fp, row["item_id"], row["part_id"], task))
        elif row["endpoint_fingerprint"] != fp:
            derived.execute("UPDATE model_input_queue SET status = 'failed', detail = ? "
                            "WHERE item_id = ? AND part_id = ? AND task = ?",
                            ("endpoint profile changed after this work was queued; requeue explicitly",
                             row["item_id"], row["part_id"], task))
            counts["failed-changed-endpoint"] = counts.get("failed-changed-endpoint", 0) + 1
            continue
        meta = source.execute("""SELECT i.source_id, i.subject, i.from_addr, p.filename, p.mime_type, p.size FROM
                                 parts p JOIN items i USING (item_id) WHERE p.item_id = ? AND p.part_id = ?""",
                              (row["item_id"], row["part_id"])).fetchone()
        instruction, schema = TASKS[task]
        prompt = (f"{instruction}\nFile name: {meta['filename']}\nType: {meta['mime_type']}\nSize: {meta['size']}\n"
                  f"Subject: {meta['subject']}\nSender domain: {(meta['from_addr'] or '').partition('@')[2]}")
        from towpath.mail.privacy import targets  # the message and its sender, for the owner's marks

        result = infer(config, task, prompt, "metadata", schema,
                       targets(meta["source_id"], [row["item_id"]], [meta["from_addr"]]), background=False)
        # Disabled work (for example, no probe yet) and work the model couldn't take now stay queued, pinned to
        # this profile.
        status = {"ok": "done", "manual-review": "manual-review", "disabled": "queued", "unavailable": "queued"}.get(
            result["status"], result["status"])
        derived.execute("UPDATE model_input_queue SET status = ?, detail = ? "
                        "WHERE item_id = ? AND part_id = ? AND task = ?",
                        (status, result.get("reason"), row["item_id"], row["part_id"], task))
        if result["status"] == "ok":
            derived.execute("""INSERT OR REPLACE INTO model_results (item_id, part_id, task, output, endpoint_id,
                               endpoint_fingerprint, method, at) VALUES (?,?,?,?,?,?,?,?)""",
                            (row["item_id"], row["part_id"], task, json.dumps(result["output"]), endpoint_id, fp,
                             result["method"], now()))
        counts[status] = counts.get(status, 0) + 1
        derived.commit()
    derived.commit()
    derived.close()
    source.close()
    return counts
