"""The inference gateway: the only Towpath code that calls a model.

Callers name a task, never an endpoint. Every call is checked against the
task binding, the endpoint's destination, each item's model use, the data
class ceiling, and the owner's grants, then recorded in the ledger. Fallbacks
change method on the same endpoint; they never switch endpoints. A model that
is busy, unreachable or failing ends the call at once. Background work yields
to everyone else using the model (pacing).
"""

import hashlib
import json
import time
from datetime import datetime, timezone

from towpath.models import pacing
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
        if use == "no-cloud" and endpoint.destination == "third-party":
            return "refused", f"item {item} is kept from cloud models and endpoint {endpoint.id} is one", endpoint
    if data_class not in endpoint.allow_data:
        return "refused", f"endpoint {endpoint.id} does not allow data class {data_class}", endpoint
    if not local and data_class != "synthetic" and not _granted(config, endpoint, data_class):
        return "refused", f"no grant for {data_class} on {endpoint.destination} endpoint {endpoint.id}", endpoint
    return "ok", "", endpoint


def background_wait(config, task: str) -> float:
    """Seconds before the task's model takes background work again (0: now), so a caller can skip preparing it."""
    endpoint = config.endpoints[config.tasks[task]]
    return pacing.wait(config.store_dir, endpoint.id, endpoint.timeout_seconds)


def infer(config, task: str, prompt: str, data_class: str, output_schema: dict, item_ids: list[str] = (),
          prompt_version: str = "v0", *, background: bool) -> dict:
    """Run a structured task through the gateway. Never raises for policy or model failures.

    ``background`` is work nobody is waiting on: it yields to everyone else using the model. A model that couldn't
    take the call now answers status "unavailable" with a failure of "busy", "transient" (no answer, a server
    error or a rate limit) or "paced" (background work waits its turn; nothing was sent)."""
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

    if background:
        held = pacing.wait(config.store_dir, endpoint.id, endpoint.timeout_seconds)
        if held:
            return {"status": "unavailable", "failure": "paced",
                    "reason": f"background model work waits {held:.0f} s more"}

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
                                              messages=[{"role": "user", "content": instruction + extra}],
                                              extra_headers=pacing.HEADERS if background else None, **kwargs)
        return resp

    import jsonschema
    import openai
    # Another format can't help a model that is busy, unreachable or failing: the call ends at once.
    UNAVAILABLE = (openai.ConflictError, openai.APIConnectionError, openai.RateLimitError,  # noqa: N806
                   openai.InternalServerError)
    RECOVERABLE = (openai.APIError, jsonschema.ValidationError, ValueError, LookupError, TypeError)  # noqa: N806
    started = time.monotonic()

    def finish(result: dict) -> dict:
        if background:
            pacing.record(config.store_dir, endpoint.id, started, result.get("failure"))
        return result

    for method in methods + ["re-ask"]:
        use = methods[-1] if method == "re-ask" else method
        extra = ("\n\nYour previous answer was invalid: " + "; ".join(errors[-2:])) if method == "re-ask" else ""
        try:
            resp = ask(use, extra)
            served = getattr(resp, "model", None)
            value = _validate(resp.choices[0].message.content or "", output_schema)
        except UNAVAILABLE as exc:
            reason = f"{method}: {exc.__class__.__name__}"
            _ledger(config, **base, endpoint_id=endpoint.id, fingerprint=fp, destination=endpoint.destination,
                    model=endpoint.model, method=method, outcome="unavailable", detail=reason)
            return finish({"status": "unavailable", "reason": reason,
                           "failure": "busy" if isinstance(exc, openai.ConflictError) else "transient"})
        except RECOVERABLE as exc:
            errors.append(f"{method}: {exc.__class__.__name__}")
            continue
        _ledger(config, **base, endpoint_id=endpoint.id, fingerprint=fp, destination=endpoint.destination,
                model=endpoint.model, served_model=served, method=method, outcome="ok",
                usage=json.dumps(getattr(getattr(resp, "usage", None), "model_dump", lambda: None)()))
        return finish({"status": "ok", "output": value, "method": method, "endpoint_id": endpoint.id,
                       "fingerprint": fp})

    _ledger(config, **base, endpoint_id=endpoint.id, fingerprint=fp, destination=endpoint.destination,
            model=endpoint.model, served_model=served, method="exhausted", outcome="manual-review",
            detail="; ".join(errors))
    return finish({"status": "manual-review", "reason": "; ".join(errors)})
