"""Acceptance checks for the first slice, continued: digests, no write paths, rebuilds, roles, and hostile content."""

import ast
import json
import sqlite3

import pytest

from tests.test_first_slice import REPO, count, proposals
from towpath import config as config_mod
from towpath import connect, decisions, fieldmask, scan
from towpath.adapters import FixtureGmailClient
from towpath.fixtures import generator
from towpath.fixtures.domains import check_paths, violations
from towpath.stores import open_store

# 12. Proposal digests are stable across processes
def test_12_digests_stable_across_processes(tmp_path):
    ids = []
    for name in ("one", "two"):
        root = tmp_path / name
        generator.generate(root)
        from tests.conftest import Workspace  # noqa: PLC0415
        w = Workspace.__new__(Workspace)
        w.root = root
        w.config = config_mod.load(root / "towpath.toml")
        w.cli("connect", "sync")
        w.cli("scan", "run")
        w.cli("connect", "fetch-requests")
        w.cli("scan", "run")
        ids.append({(p["proposal_id"], p["digest"]) for p in w.cli_json("proposals", "list")})
    assert ids[0] == ids[1] and ids[0]


# 13. No module, configuration key, or dependency for writing to a mailbox or destination exists
def test_13_no_write_paths(ws, tmp_path):
    src = REPO / "src" / "towpath"
    forbidden = {"send", "modify", "batchModify", "trash", "untrash", "insert", "import_", "delete_message",
                 "upload", "deliver", "post_document", "execute"}
    for path in src.rglob("*.py"):
        tree = ast.parse(path.read_text())
        defined = {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        assert not defined & forbidden, f"{path} defines {defined & forbidden}"
    public = {name for name in dir(FixtureGmailClient) if not name.startswith("_")}
    assert public <= {"get_profile", "list_messages", "get_message", "get_attachment", "list_history"}
    pyproject = (REPO / "pyproject.toml").read_text()
    deps = pyproject.split("dependencies = ", 1)[1].split("\n", 1)[0]
    assert deps == '["typer>=0.12"]'
    bad = tmp_path / "bad.toml"
    bad.write_text((ws.root / "towpath.toml").read_text().replace(
        'path = "account_a.json"', 'path = "account_a.json"\nwrite_credential = "env:X"'))
    with pytest.raises(config_mod.ConfigError):
        config_mod.load(bad)


# 14. Deleting the derived store and rerunning reproduces proposals; decisions remain
def test_14_rebuild_keeps_decisions(ws):
    ws.full_pipeline()
    target = sorted(proposals(ws))[0]
    decisions.dismiss(ws.config, target)
    ws.scan_all()
    before = set(proposals(ws))
    item = ws.item(ws.summary["statement_jan"])
    decisions.set_item(ws.config, item, audience="shareable", model_use="local-only")
    (ws.config.store_dir / "derived.db").unlink()
    ws.scan_all()
    assert set(proposals(ws)) == before
    assert target not in proposals(ws)
    db = ws.db("decisions")
    settings = {(r["key"], r["value"]) for r in db.execute("SELECT * FROM settings WHERE target_id = ?", (item,))}
    db.close()
    assert settings == {("audience", "shareable"), ("model_use", "local-only")}


# 15. An item excluded from model use never enters the model-input queue, across rebuilds
def test_15_model_use_excluded(ws):
    ws.sync_all()
    excluded = ws.item(ws.summary["statement_jan"])
    decisions.set_item(ws.config, excluded, model_use="excluded")
    ws.scan_all()
    queued = {r["item_id"] for r in scan.model_queue(ws.config)}
    assert queued and excluded not in queued
    (ws.config.store_dir / "derived.db").unlink()
    ws.scan_all()
    assert excluded not in {r["item_id"] for r in scan.model_queue(ws.config)}


# 16. connect and worker run as separate processes; the worker opens the source index read-only
def test_16_roles_and_processes(ws):
    ws.cli("connect", "sync")
    ws.cli("scan", "run")
    ws.cli("connect", "fetch-requests")
    out = ws.cli_json("scan", "run")
    assert sum(r["proposed"] for r in out) > 0
    db = open_store(ws.config.store_dir, "source", "worker")
    with pytest.raises(sqlite3.OperationalError):
        db.execute("DELETE FROM items")
    db.close()
    db = open_store(ws.config.store_dir, "decisions", "worker")
    with pytest.raises(sqlite3.OperationalError):
        db.execute("INSERT INTO settings VALUES ('x', 'model_use', 'excluded', 'w', 'now')")
    db.close()


# 17. The prompt-injection fixture has no effect on scans or proposals
def test_17_prompt_injection_inert(ws):
    ws.full_pipeline()
    injection = ws.item(ws.summary["injection"])
    related = [p for p in proposals(ws).values() if p["body"]["item_id"] == injection]
    assert len(related) == 1 and related[0]["body"]["action"] == "deliver"
    assert related[0]["body"]["file_name"] == "instructions.pdf"
    db = ws.db("source")
    body_fetched = db.execute("""SELECT COUNT(*) FROM fetches f JOIN parts p USING (item_id, part_id)
                                 WHERE f.item_id = ? AND p.mime_type LIKE 'text/%'""", (injection,)).fetchone()[0]
    db.close()
    assert body_fetched == 0
    assert count(ws, "decisions", "SELECT COUNT(*) FROM decision_log") == 0


# 18. Rerunning scans creates no duplicate requests or proposals
def test_18_reruns_are_idempotent(ws):
    ws.sync_all()
    ws.scan_all()
    requests = count(ws, "queue", "SELECT COUNT(*) FROM content_requests")
    ws.scan_all()
    assert count(ws, "queue", "SELECT COUNT(*) FROM content_requests") == requests
    connect.fetch_requests(ws.config)
    again = connect.fetch_requests(ws.config)
    assert again["fetched"] == 0
    ws.scan_all()
    first = set(proposals(ws))
    ws.scan_all()
    assert set(proposals(ws)) == first
    assert count(ws, "derived", "SELECT COUNT(*) FROM proposals") == len(first)


# 19. A repository check rejects fixture addresses outside reserved domains
def test_19_reserved_domains_only(ws):
    assert check_paths([ws.root, REPO / "tests", REPO / "src"]) == {}
    outside = "someone" + "@" + "mail.invalid-provider.net"  # built at runtime so this file passes the check
    assert violations(f"contact {outside} or a@b.example.com") == [outside]


# Parser robustness and unparseable reporting (fixture cases)
def test_parser_robustness(ws):
    ws.sync_all()
    db = ws.db("source")
    bad = db.execute("SELECT * FROM items WHERE native_id = ?", (ws.summary["bad_date"],)).fetchone()
    assert "invalid date header" in json.loads(bad["problems"]) and bad["date_utc"] is None
    assert db.execute("SELECT rfc_message_id FROM items WHERE native_id = ?",
                      (ws.summary["no_message_id"],)).fetchone()[0] is None
    latin = db.execute("SELECT * FROM items WHERE native_id = ?", (ws.summary["latin1"],)).fetchone()
    assert latin["subject"] == "Menü du jour"
    charset = db.execute("SELECT charset FROM parts WHERE item_id = ?", (latin["item_id"],)).fetchone()[0]
    assert charset == "iso-8859-1"
    db.close()
    report = connect.coverage(ws.config, "src_a")[0]
    assert [u["native_id"] for u in report["unparseable"]] == [ws.summary["bad_date"]]


def test_fieldmask_excludes_nested_data():
    message = {"id": "1", "payload": {"partId": "", "mimeType": "multipart/mixed", "body": {"size": 0},
                                      "parts": [{"partId": "0", "mimeType": "text/plain",
                                                 "body": {"size": 3, "data": "YWJj"}}]}}
    masked = fieldmask.apply(message, fieldmask.parse(fieldmask.message_mask(3)))
    assert "data" not in json.dumps(masked)
    assert masked["payload"]["parts"][0]["body"] == {"size": 3}


def test_example_config_is_valid_and_placeholder_only():
    cfg = config_mod.load(REPO / "examples" / "towpath.example.toml")
    assert {s.adapter for s in cfg.sources.values()} == {"gmail", "paperless", "immich"}
    assert cfg.endpoints["local"].destination == "this-machine"
    text = (REPO / "examples" / "towpath.example.toml").read_text()
    assert violations(text) == []
    refs = [s.credential for s in cfg.sources.values() if s.credential] + [e.credential for e in cfg.endpoints.values()]
    assert refs and all(r == "none" or r.startswith(("file:", "env:")) for r in refs)


def test_ctrl_c_during_full_sync_resumes(ws, monkeypatch):
    original = FixtureGmailClient.get_message
    calls = {"n": 0}

    def flaky(self, message_id, fields=None):
        calls["n"] += 1
        if calls["n"] == 8:
            raise KeyboardInterrupt
        return original(self, message_id, fields)

    monkeypatch.setattr(FixtureGmailClient, "get_message", flaky)
    with pytest.raises(KeyboardInterrupt):
        connect.sync(ws.config, "src_a")
    monkeypatch.setattr(FixtureGmailClient, "get_message", original)
    resumed = connect.sync(ws.config, "src_a")
    assert resumed["kind"] == "resumed-full" and resumed["complete"]
    assert resumed["indexed_total"] == ws.summary["messages_a"] and resumed["seen"] == ws.summary["messages_a"] - 7
