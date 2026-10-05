"""Slice 1b: the model endpoint contract, against a loopback stub (docs/first-slice.md)."""

import json
from pathlib import Path

import pytest

pytest.importorskip("openai")
pytest.importorskip("jsonschema")

from towpath import decisions, scan  # noqa: E402
from towpath.models import gateway  # noqa: E402
from towpath.models.profiles import ProfileError  # noqa: E402

SCHEMA = gateway.STATEMENT_SCHEMA


def endpoint(name, url, destination="this-machine", model="stub-model", kind="chat",
             allow='["synthetic", "metadata", "content"]', credential="none"):
    return f"""
[endpoints.{name}]
base_url = "{url}/v1"
credential = "{credential}"
model = "{model}"
kind = "{kind}"
destination = "{destination}"
allow_data = {allow}
timeout_seconds = 5
"""


def bind(ws, text, tasks):
    lines = "\n".join(f'"{task}" = "{eid}"' for task, eid in tasks.items())
    return ws.add_config(text + ("\n[tasks]\n" + lines + "\n" if tasks else ""))


# 1b-1. With no endpoint profile, the classifier reports Disabled and the scan still runs
def test_1b_1_no_endpoint_disables_cleanly(ws):
    ws.sync_all()
    results = ws.scan_all()
    assert results["pdf-to-documents"]["candidates"] > 0
    out = gateway.infer(ws.config, "doc.is_statement", "synthetic", "metadata", SCHEMA)
    assert out["status"] == "disabled" and "no endpoint" in out["reason"]
    assert gateway.run_queue(ws.config) == {"disabled": len(scan.model_queue(ws.config))}


# 1b-2. Probe records exactly the capabilities the stub offers, using synthetic prompts only
def test_1b_2_probe_records_capabilities(ws, openai_stub):
    chat = openai_stub(models=False, json_schema=False, json_object=True)
    emb = openai_stub(embeddings=True)
    bind(ws, endpoint("chat", chat.url) + endpoint("emb", emb.url, kind="embeddings"), {})
    report = gateway.probe(ws.config, "chat")
    assert report["capabilities"] == {"models_list": False, "chat": True, "json_schema": False, "json_object": True}
    assert report["served_model"] == "stub-model-served" and report["usage"] is True
    prompts = {r["body"]["messages"][-1]["content"] for r in chat.chat_requests()}
    assert prompts == {"Reply with the word ok.", 'Return the JSON object {"ok": true}.'}
    report = gateway.probe(ws.config, "emb")
    assert report["capabilities"]["embeddings"] is True and report["dimensions"] == 3
    assert report["model_listed"] is True


# 1b-3. Structured output falls back json_schema -> json_object -> prompt-only, then to manual review
def test_1b_3_fallback_ladder(ws, openai_stub):
    stub = openai_stub(answers=['{"is_statement": tru', '{"is_statement": "yes"}',
                                '```json\n{"is_statement": false, "confidence": 0.2}\n```'])
    bind(ws, endpoint("local", stub.url), {"doc.is_statement": "local"})
    gateway.probe(ws.config, "local")
    out = gateway.infer(ws.config, "doc.is_statement", "synthetic test", "metadata", SCHEMA)
    assert out["status"] == "ok" and out["method"] == "prompt"
    assert out["output"] == {"is_statement": False, "confidence": 0.2}
    formats = [(r["body"].get("response_format") or {}).get("type") for r in stub.chat_requests()[-3:]]
    assert formats == ["json_schema", "json_object", None]

    stub.answers[:] = ["not json"] * 4
    out = gateway.infer(ws.config, "doc.is_statement", "synthetic test", "metadata", SCHEMA)
    assert out["status"] == "manual-review"
    assert "invalid" in stub.chat_requests()[-1]["body"]["messages"][-1]["content"]  # the one re-ask


def test_1b_3b_repairable_json_on_first_rung(ws, openai_stub):
    stub = openai_stub(json_schema=True, answers=['{"is_statement": true, "confidence": 0.7'])
    bind(ws, endpoint("local", stub.url), {"doc.is_statement": "local"})
    gateway.probe(ws.config, "local")
    out = gateway.infer(ws.config, "doc.is_statement", "synthetic", "metadata", SCHEMA)
    assert out["status"] == "ok" and out["method"] == "json_schema"


# 1b-4. A this-machine profile with a non-loopback host is rejected; bundled requires a bundled host
def test_1b_4_destination_checks(ws):
    bind(ws, endpoint("far", "http://192.0.2.10:8080") + endpoint("bun", "http://models:8080", "bundled"),
         {"doc.is_statement": "far", "other": "bun"})
    with pytest.raises(ProfileError):
        gateway.probe(ws.config, "far")
    status, reason, _ = gateway.check_policy(ws.config, "doc.is_statement", "metadata", [])
    assert status == "refused" and "loopback" in reason
    status, reason, _ = gateway.check_policy(ws.config, "other", "metadata", [])
    assert status == "refused" and "bundled" in reason
    path = ws.root / "towpath.toml"
    path.write_text('bundled_hosts = ["models"]\n' + path.read_text())
    ws.replace_config("", "")
    assert gateway.check_policy(ws.config, "other", "metadata", [])[0] == "ok"


# 1b-5. A self-hosted profile without a grant refuses content and metadata
def test_1b_5_self_hosted_needs_grants(ws, openai_stub):
    stub = openai_stub()
    bind(ws, endpoint("lan", stub.url, "self-hosted"), {"doc.is_statement": "lan"})
    gateway.probe(ws.config, "lan")  # synthetic prompts need no grant
    for data_class in ("metadata", "content"):
        out = gateway.infer(ws.config, "doc.is_statement", "x", data_class, SCHEMA)
        assert out["status"] == "refused" and "no grant" in out["reason"]
    before = len(stub.chat_requests())
    decisions.grant_model(ws.config, "lan", "metadata")
    assert gateway.infer(ws.config, "doc.is_statement", "x", "metadata", SCHEMA)["status"] == "ok"
    assert gateway.infer(ws.config, "doc.is_statement", "x", "content", SCHEMA)["status"] == "refused"
    assert len(stub.chat_requests()) == before + 1


# 1b-6. Excluded items are refused even with every grant; local-only items are refused by self-hosted endpoints
def test_1b_6_item_model_use(ws, openai_stub):
    stub = openai_stub()
    bind(ws, endpoint("lan", stub.url, "self-hosted") + endpoint("local", stub.url),
         {"doc.is_statement": "lan", "local.task": "local"})
    for cls in ("metadata", "content", "attachments", "derived-personal"):
        decisions.grant_model(ws.config, "lan", cls)
    decisions.set_item(ws.config, "itm_excluded", model_use="excluded")
    decisions.set_item(ws.config, "itm_local", model_use="local-only")
    assert gateway.check_policy(ws.config, "doc.is_statement", "metadata", ["itm_excluded"])[0] == "refused"
    assert gateway.check_policy(ws.config, "local.task", "metadata", ["itm_excluded"])[0] == "refused"
    status, reason, _ = gateway.check_policy(ws.config, "doc.is_statement", "metadata", ["itm_local"])
    assert status == "refused" and "local-only" in reason
    assert gateway.check_policy(ws.config, "local.task", "metadata", ["itm_local"])[0] == "ok"


# 1b-7. Changing the profile's model voids its grants and fails queued work instead of rerouting
def test_1b_7_profile_change_fails_queued_work(ws, openai_stub):
    stub = openai_stub()
    ws.sync_all()
    ws.scan_all()
    queued = len(scan.model_queue(ws.config))
    bind(ws, endpoint("lan", stub.url, "self-hosted"), {"doc.is_statement": "lan"})
    decisions.grant_model(ws.config, "lan", "metadata")
    counts = gateway.run_queue(ws.config)  # no probe yet: work stays queued, pinned to this profile
    assert counts == {"queued": queued}
    assert {r["endpoint_fingerprint"] for r in scan.model_queue(ws.config)} != {None}

    ws.replace_config('model = "stub-model"', 'model = "other-model"')
    gateway.probe(ws.config, "lan")
    sent = len(stub.chat_requests())
    counts = gateway.run_queue(ws.config)
    assert counts == {"failed-changed-endpoint": queued}
    assert len(stub.chat_requests()) == sent  # nothing rerouted to the changed endpoint
    status, reason, _ = gateway.check_policy(ws.config, "doc.is_statement", "metadata", [])
    assert status == "refused" and "no grant" in reason


# 1b-8. The prompt-injection fixture produces at most a proposal, never an approval or other record
def test_1b_8_adversarial_output_is_contained(ws, openai_stub):
    stub = openai_stub(default_answer='{"action": "delete_all", "forward_to": "x@attacker.example.net"}')
    ws.sync_all()
    ws.scan_all()
    bind(ws, endpoint("local", stub.url), {"doc.is_statement": "local"})
    gateway.probe(ws.config, "local")
    decisions_before = _dump(ws, "decisions")
    counts = gateway.run_queue(ws.config)
    assert set(counts) == {"manual-review"}
    assert _dump(ws, "decisions") == decisions_before
    db = ws.db("derived")
    assert db.execute("SELECT COUNT(*) FROM model_results").fetchone()[0] == 0
    db.close()


def test_valid_model_results_are_recorded(ws, openai_stub):
    stub = openai_stub()
    ws.sync_all()
    ws.scan_all()
    bind(ws, endpoint("local", stub.url), {"doc.is_statement": "local"})
    gateway.probe(ws.config, "local")
    excluded = sorted(r["item_id"] for r in scan.model_queue(ws.config))[0]
    decisions.set_item(ws.config, excluded, model_use="excluded")
    counts = gateway.run_queue(ws.config)
    assert counts.get("done", 0) == len(scan.model_queue(ws.config)) - 1
    assert counts.get("refused") == 1
    sent = json.dumps([r["body"] for r in stub.chat_requests()])
    assert "File name:" in sent and "Sender domain:" in sent
    db = ws.db("ledger")
    rows = db.execute("SELECT * FROM calls WHERE task = 'doc.is_statement'").fetchall()
    db.close()
    assert rows and all(r["input_sha256"] for r in rows)
    ledger_bytes = Path(ws.config.store_dir / "ledger.db").read_bytes()
    assert b"File name:" not in ledger_bytes  # prompts are fingerprinted, not stored


def test_no_environment_defaults_reach_the_endpoint(ws, openai_stub, monkeypatch):
    stub = openai_stub()
    for var, value in {"OPENAI_BASE_URL": "http://192.0.2.1:9/v1", "OPENAI_API_KEY": "env-key",
                       "OPENAI_ORG_ID": "org-env", "OPENAI_PROJECT_ID": "proj-env",
                       "OPENAI_ADMIN_KEY": "admin-env"}.items():
        monkeypatch.setenv(var, value)
    monkeypatch.setenv("TOWPATH_TEST_KEY", "profile-key")
    bind(ws, endpoint("local", stub.url, credential="env:TOWPATH_TEST_KEY"), {})
    gateway.probe(ws.config, "local")
    for req in stub.requests:
        assert req["headers"]["authorization"] == "Bearer profile-key"
        assert "openai-organization" not in req["headers"] and "openai-project" not in req["headers"]


def _dump(ws, store):
    db = ws.db(store)
    tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
    data = {t: [tuple(r) for r in db.execute(f"SELECT * FROM {t}")] for t in tables}
    db.close()
    return data
