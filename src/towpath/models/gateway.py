"""The inference gateway: the only Towpath code that calls a model.

Callers name a task, never an endpoint. Every call is checked against the
task binding, the endpoint's destination, each item's model use, the data
class ceiling, and the owner's grants, then recorded in the ledger. Fallbacks
change method on the same endpoint; they never switch endpoints.
"""

import hashlib
import json
from datetime import datetime, timezone

from towpath.models.profiles import LOCAL_DESTINATIONS, ProfileError, check_destination, fingerprint, make_client
from towpath.stores import open_store

ROLE = "worker"
PROBE_SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"],
                "additionalProperties": False}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ledger(config, **row) -> None:
    db = open_store(config.store_dir, "ledger", ROLE)
    cols = ", ".join(row)
    db.execute(f"INSERT INTO calls (at, {cols}) VALUES (?, {', '.join('?' for _ in row)})", (now(), *row.values()))
    db.commit()
    db.close()


# -- capability probing ---------------------------------------------------------

def probe(config, endpoint_id: str) -> dict:
    """Probe an endpoint with synthetic prompts only and record what works."""
    import openai

    endpoint = config.endpoints[endpoint_id]
    check_destination(endpoint, config.bundled_hosts)
    client = make_client(endpoint, config.root)
    report = {"endpoint_id": endpoint_id, "fingerprint": fingerprint(endpoint), "model": endpoint.model,
              "kind": endpoint.kind, "capabilities": {}, "served_model": None, "usage": False, "errors": {}}
    caps = report["capabilities"]

    def attempt(name, fn):
        try:
            return fn()
        except openai.APIConnectionError as exc:
            report["errors"][name] = f"connection: {exc.__class__.__name__}"
        except openai.APIStatusError as exc:
            report["errors"][name] = f"status {exc.status_code}"
        except (ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
            report["errors"][name] = f"invalid response: {exc.__class__.__name__}"
        return None

    models = attempt("models_list", lambda: [m.id for m in client.models.list()])
    caps["models_list"] = models is not None
    report["model_listed"] = bool(models) and endpoint.model in models

    if endpoint.kind == "embeddings":
        emb = attempt("embeddings", lambda: client.embeddings.create(
            model=endpoint.model, input=["towpath probe alpha", "towpath probe beta"]))
        caps["embeddings"] = bool(emb and len(emb.data) == 2)
        report["dimensions"] = len(emb.data[0].embedding) if caps["embeddings"] else None
    else:
        def chat(**kw):
            return client.chat.completions.create(model=endpoint.model, max_tokens=20, **kw)

        basic = attempt("chat", lambda: chat(messages=[{"role": "user", "content": "Reply with the word ok."}]))
        caps["chat"] = bool(basic and basic.choices)
        if basic is not None:
            report["served_model"] = getattr(basic, "model", None)
            report["usage"] = getattr(basic, "usage", None) is not None

        def structured(fmt):
            resp = chat(messages=[{"role": "user", "content": 'Return the JSON object {"ok": true}.'}],
                        response_format=fmt)
            return _validate(resp.choices[0].message.content or "", PROBE_SCHEMA, repair=False)

        schema_format = {"type": "json_schema",
                         "json_schema": {"name": "probe", "schema": PROBE_SCHEMA, "strict": True}}
        caps["json_schema"] = attempt("json_schema", lambda: structured(schema_format)) is not None
        caps["json_object"] = attempt("json_object", lambda: structured({"type": "json_object"})) is not None

    db = open_store(config.store_dir, "ledger", ROLE)
    db.execute("INSERT OR REPLACE INTO capability_reports (endpoint_id, fingerprint, at, report) VALUES (?, ?, ?, ?)",
               (endpoint_id, report["fingerprint"], now(), json.dumps(report, sort_keys=True)))
    db.commit()
    db.close()
    _ledger(config, task="probe", endpoint_id=endpoint_id, fingerprint=report["fingerprint"],
            destination=endpoint.destination, model=endpoint.model, served_model=report["served_model"],
            data_class="synthetic", outcome="ok", detail=json.dumps(caps, sort_keys=True))
    return report


def capability_report(config, endpoint_id: str) -> dict | None:
    endpoint = config.endpoints[endpoint_id]
    db = open_store(config.store_dir, "ledger", "web")
    row = db.execute("SELECT report FROM capability_reports WHERE endpoint_id = ? AND fingerprint = ?",
                     (endpoint_id, fingerprint(endpoint))).fetchone()
    db.close()
    return json.loads(row["report"]) if row else None


# -- structured output -------------------------------------------------------------

def _validate(text: str, schema: dict, repair: bool = True):
    import jsonschema

    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{"):] if "{" in text else text
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        if not repair:
            raise ValueError("not JSON") from None
        import json_repair
        value = json_repair.loads(text)
    jsonschema.validate(value, schema)
    return value


# -- the gateway ---------------------------------------------------------------------

def _model_uses(config, item_ids: list[str]) -> dict[str, str]:
    if not item_ids:
        return {}
    db = open_store(config.store_dir, "decisions", ROLE)
    marks = ",".join("?" for _ in item_ids)
    rows = db.execute(f"SELECT target_id, value FROM settings WHERE key = 'model_use' AND target_id IN ({marks})",
                      item_ids).fetchall()
    db.close()
    return {r["target_id"]: r["value"] for r in rows}


def _granted(config, endpoint, data_class: str) -> bool:
    db = open_store(config.store_dir, "decisions", ROLE)
    row = db.execute("SELECT 1 FROM model_grants WHERE endpoint_id = ? AND fingerprint = ? AND data_class = ?",
                     (endpoint.id, fingerprint(endpoint), data_class)).fetchone()
    db.close()
    return row is not None


def check_policy(config, task: str, data_class: str, item_ids: list[str]) -> tuple[str, str, object]:
    """Return (status, reason, endpoint). Status is ok, disabled, or refused."""
    endpoint_id = config.tasks.get(task)
    if endpoint_id is None:
        return "disabled", f"no endpoint is bound to task {task}", None
    endpoint = config.endpoints[endpoint_id]
    try:
        check_destination(endpoint, config.bundled_hosts)
    except ProfileError as exc:
        return "refused", str(exc), endpoint
    local = endpoint.destination in LOCAL_DESTINATIONS
    for item, use in _model_uses(config, item_ids).items():
        if use == "excluded":
            return "refused", f"item {item} is excluded from model use", endpoint
        if use == "local-only" and not local:
            reason = f"item {item} is local-only and endpoint {endpoint.id} is {endpoint.destination}"
            return "refused", reason, endpoint
    if data_class not in endpoint.allow_data:
        return "refused", f"endpoint {endpoint.id} does not allow data class {data_class}", endpoint
    if not local and data_class != "synthetic" and not _granted(config, endpoint, data_class):
        return "refused", f"no grant for {data_class} on {endpoint.destination} endpoint {endpoint.id}", endpoint
    return "ok", "", endpoint


def infer(config, task: str, prompt: str, data_class: str, output_schema: dict, item_ids: list[str] = (),
          prompt_version: str = "v0") -> dict:
    """Run a structured task through the gateway. Never raises for policy or model failures."""
    item_ids = list(item_ids)
    input_sha = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    status, reason, endpoint = check_policy(config, task, data_class, item_ids)
    base = {"task": task, "prompt_version": prompt_version, "input_sha256": input_sha, "data_class": data_class}
    if status != "ok":
        _ledger(config, **base, endpoint_id=getattr(endpoint, "id", None),
                fingerprint=fingerprint(endpoint) if endpoint else None,
                destination=getattr(endpoint, "destination", None), outcome=status, detail=reason)
        return {"status": status, "reason": reason}

    fp = fingerprint(endpoint)
    report = capability_report(config, endpoint.id)
    if report is None:
        reason = f"endpoint {endpoint.id} has no capability report for its current profile; run a probe"
        _ledger(config, **base, endpoint_id=endpoint.id, fingerprint=fp, destination=endpoint.destination,
                outcome="disabled", detail=reason)
        return {"status": "disabled", "reason": reason}

    caps = report["capabilities"]
    methods = [m for m in ("json_schema", "json_object") if caps.get(m)] + ["prompt"]
    client = make_client(endpoint, config.root)
    instruction = (f"{prompt}\n\nRespond with only a JSON object matching this JSON Schema:\n"
                   f"{json.dumps(output_schema, sort_keys=True)}")
    errors: list[str] = []
    served = None

    def ask(method: str, extra: str = ""):
        kwargs = {}
        if method == "json_schema":
            kwargs["response_format"] = {"type": "json_schema",
                                         "json_schema": {"name": task.replace(".", "_"), "schema": output_schema,
                                                         "strict": True}}
        elif method == "json_object":
            kwargs["response_format"] = {"type": "json_object"}
        resp = client.chat.completions.create(model=endpoint.model,
                                              messages=[{"role": "user", "content": instruction + extra}], **kwargs)
        return resp

    import jsonschema
    import openai
    RECOVERABLE = (openai.APIError, jsonschema.ValidationError, ValueError, LookupError, TypeError)  # noqa: N806

    for method in methods + ["re-ask"]:
        use = methods[-1] if method == "re-ask" else method
        extra = ("\n\nYour previous answer was invalid: " + "; ".join(errors[-2:])) if method == "re-ask" else ""
        try:
            resp = ask(use, extra)
            served = getattr(resp, "model", None)
            value = _validate(resp.choices[0].message.content or "", output_schema)
        except RECOVERABLE as exc:
            errors.append(f"{method}: {exc.__class__.__name__}")
            continue
        _ledger(config, **base, endpoint_id=endpoint.id, fingerprint=fp, destination=endpoint.destination,
                model=endpoint.model, served_model=served, method=method, outcome="ok",
                usage=json.dumps(getattr(getattr(resp, "usage", None), "model_dump", lambda: None)()))
        return {"status": "ok", "output": value, "method": method, "endpoint_id": endpoint.id, "fingerprint": fp}

    _ledger(config, **base, endpoint_id=endpoint.id, fingerprint=fp, destination=endpoint.destination,
            model=endpoint.model, served_model=served, method="exhausted", outcome="manual-review",
            detail="; ".join(errors))
    return {"status": "manual-review", "reason": "; ".join(errors)}


# -- the model-input queue -----------------------------------------------------------

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
        meta = source.execute("""SELECT i.subject, i.from_addr, p.filename, p.mime_type, p.size FROM parts p
                                 JOIN items i USING (item_id) WHERE p.item_id = ? AND p.part_id = ?""",
                              (row["item_id"], row["part_id"])).fetchone()
        instruction, schema = TASKS[task]
        prompt = (f"{instruction}\nFile name: {meta['filename']}\nType: {meta['mime_type']}\nSize: {meta['size']}\n"
                  f"Subject: {meta['subject']}\nSender domain: {(meta['from_addr'] or '').partition('@')[2]}")
        result = infer(config, task, prompt, "metadata", schema, [row["item_id"]])
        # Disabled work (for example, no probe yet) stays queued, pinned to this profile.
        status = {"ok": "done", "manual-review": "manual-review", "disabled": "queued"}.get(
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
