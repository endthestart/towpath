"""Acceptance checks for the first slice (docs/first-slice.md), numbered as there."""

import ast
import json
import sqlite3
from pathlib import Path

import pytest

from towpath import config as config_mod
from towpath import connect, decisions, fieldmask, scan
from towpath.adapters import FixtureGmailClient, GmailConnector
from towpath.fixtures import generator
from towpath.fixtures.domains import check_paths, violations
from towpath.stores import open_store

REPO = Path(__file__).resolve().parents[1]


def count(ws, store, sql, *args):
    db = ws.db(store)
    value = db.execute(sql, args).fetchone()[0]
    db.close()
    return value


def proposals(ws, state="proposed"):
    return {p["proposal_id"]: p for p in scan.list_proposals(ws.config, state)}


# 1. Syncing twice creates no new items and one new run record
def test_01_second_sync_adds_only_a_run(ws):
    ws.sync_all()
    items = count(ws, "source", "SELECT COUNT(*) FROM items")
    runs = count(ws, "source", "SELECT COUNT(*) FROM runs WHERE source_id = 'src_a'")
    second = connect.sync(ws.config, "src_a")
    assert second["kind"] == "incremental" and second["seen"] == 0
    assert count(ws, "source", "SELECT COUNT(*) FROM items") == items
    assert count(ws, "source", "SELECT COUNT(*) FROM runs WHERE source_id = 'src_a'") == runs + 1


# 2. An interrupted sync is marked incomplete; the next run resumes and coverage shows the gap closing
def test_02_interrupted_sync_resumes(ws):
    first = connect.sync(ws.config, "src_a", interrupt_after=15)
    assert first["complete"] is False and first["indexed_total"] == 15
    second = connect.sync(ws.config, "src_a")
    assert second["kind"] == "resumed-full" and second["complete"] is True
    assert second["seen"] == ws.summary["messages_a"] - 15
    report = connect.coverage(ws.config, "src_a")
    assert [r["complete"] for r in report] == [0, 1]
    assert [r["items_indexed_total"] for r in report] == [15, ws.summary["messages_a"]]


# 3. An expired cursor triggers a full rescan with the same final items
def test_03_expired_cursor_full_rescan(ws):
    connect.sync(ws.config, "src_a")
    before = count(ws, "source", "SELECT COUNT(*) FROM items WHERE absent_since_run IS NULL")
    generator.expire_cursor(ws.root)
    again = connect.sync(ws.config, "src_a")
    assert again["kind"] == "full-after-expired-cursor" and again["complete"]
    assert again["absent"] == 0
    assert count(ws, "source", "SELECT COUNT(*) FROM items WHERE absent_since_run IS NULL") == before


# 4. The deleted message is recorded absent, keeps its history, and its proposals go stale
def test_04_deletion_marks_absent_and_stale(ws):
    ws.full_pipeline()
    jan = ws.item(ws.summary["statement_jan"])
    jan_proposals = [p for p in proposals(ws).values() if p["body"]["item_id"] == jan]
    assert len(jan_proposals) == 1
    ws.advance()
    run = connect.sync(ws.config, "src_a")
    assert run["kind"] == "incremental" and run["absent"] == 1
    db = ws.db("source")
    row = db.execute("SELECT * FROM items WHERE item_id = ?", (jan,)).fetchone()
    assert row["absent_since_run"] == run["run_id"]
    assert db.execute("SELECT COUNT(*) FROM observations WHERE item_id = ?", (jan,)).fetchone()[0] >= 1
    db.close()
    ws.scan_all()
    assert proposals(ws, "stale").keys() == {jan_proposals[0]["proposal_id"]}


# 5. Labels changed between runs are recorded as dated observations; earlier ones are kept
def test_05_label_changes_are_observations(ws):
    ws.sync_all()
    ws.advance()
    run = connect.sync(ws.config, "src_a")
    for native in ws.summary["relabeled"]:
        db = ws.db("source")
        rows = db.execute("SELECT run_id, labels FROM observations WHERE item_id = ? ORDER BY run_id",
                          (ws.item(native),)).fetchall()
        db.close()
        assert len(rows) == 2
        assert "INBOX" in json.loads(rows[0]["labels"])
        assert rows[1]["run_id"] == run["run_id"]
        assert json.loads(rows[1]["labels"]) == ["CATEGORY_PROMOTIONS", "Label_Newsletters"]


# 6. The shared Message-ID yields two items and one match record
def test_06_shared_message_id_links_not_merges(ws):
    ws.sync_all()
    shared = ws.summary["shared_message_id"]
    assert count(ws, "source", "SELECT COUNT(*) FROM items WHERE rfc_message_id = ?", shared) == 2
    assert count(ws, "source", "SELECT COUNT(*) FROM matches") == 1
    assert count(ws, "source", "SELECT COUNT(*) FROM matches WHERE strength = 'message-id-only'") == 1


# 7. Every part is listed, including deep ones, and no body data is stored, even if it arrived inline
@pytest.mark.parametrize("ignore_mask", [False, True])
def test_07_structure_without_content(ws, ignore_mask):
    connect.sync(ws.config, "src_a", ignore_mask=ignore_mask)
    db = ws.db("source")
    deep = ws.item(ws.summary["deep_pdf"])
    pdf = db.execute("SELECT * FROM parts WHERE item_id = ? AND mime_type = 'application/pdf'", (deep,)).fetchone()
    assert pdf["depth"] == 4 and pdf["part_id"] == "0.0.1.1"
    inline = db.execute("SELECT * FROM parts WHERE item_id = ? AND mime_type = 'application/pdf'",
                        (ws.item(ws.summary["inline_pdf"]),)).fetchone()
    assert inline["attachment_id"] is None and inline["size"] > 0
    assert db.execute("SELECT COUNT(*) FROM cache").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM parts WHERE sha256 IS NOT NULL").fetchone()[0] == 0
    columns = {r[1] for r in db.execute("PRAGMA table_info(parts)")} | {r[1] for r in db.execute(
        "PRAGMA table_info(items)")}
    assert not columns & {"data", "body", "raw"}
    discarded = db.execute("SELECT SUM(inline_data_discarded) FROM items").fetchone()[0]
    assert (discarded > 0) == ignore_mask
    assert db.execute("SELECT SUM(structure_truncated) FROM items").fetchone()[0] == 0
    db.close()


def test_07b_shallow_mask_is_detected(ws):
    client = FixtureGmailClient(ws.root / "account_a.json")
    shallow = GmailConnector("src_a", client, mask_depth=2)
    msg = client.get_message(ws.summary["deep_pdf"], fields=shallow.mask)
    assert shallow._record(msg)["structure_truncated"] is True
    full = GmailConnector("src_a", client)
    assert full._record(client.get_message(ws.summary["deep_pdf"], fields=full.mask))["structure_truncated"] is False


# 8. A PDF scan fetches only matching parts; other bodies are never fetched
def test_08_scans_fetch_only_matching_parts(ws):
    ws.sync_all()
    scan.run_scan(ws.config, "pdf-to-documents")
    connect.fetch_requests(ws.config)
    db = ws.db("source")
    fetched = db.execute("""SELECT p.mime_type, p.disposition FROM fetches f
                            JOIN parts p ON p.item_id = f.item_id AND p.part_id = f.part_id""").fetchall()
    assert fetched and all(r["mime_type"] == "application/pdf" and r["disposition"] == "attachment"
                           for r in fetched)
    assert db.execute("SELECT COUNT(*) FROM parts WHERE sha256 IS NOT NULL AND mime_type != 'application/pdf'"
                      ).fetchone()[0] == 0
    zip_part = db.execute("SELECT sha256 FROM parts WHERE filename = 'project-archive.zip'").fetchone()
    assert zip_part["sha256"] is None
    assert db.execute("SELECT COUNT(*) FROM cache").fetchone()[0] == len(fetched)
    db.close()


# 9. Files already in each destination are found by that destination's own hash algorithm
def test_09_presence_by_destination_algorithm(ws):
    results = ws.full_pipeline()
    assert results["pdf-to-documents"]["algorithm"] == "sha256"
    assert results["photos-to-library"]["algorithm"] == "sha1-base64"
    db = ws.db("derived")
    present = {(r["item_id"], r["dest_algorithm"]) for r in db.execute(
        "SELECT * FROM scan_matches WHERE status = 'present'")}
    db.close()
    assert present == {(ws.item(ws.summary["present_in_documents"]), "sha256"),
                       (ws.item(ws.summary["present_in_photos"]), "sha1-base64")}


# 10. The remaining matches become deliver proposals with IDs, hashes, and the destination
def test_10_absent_matches_become_proposals(ws):
    results = ws.full_pipeline()
    props = proposals(ws)
    assert len(props) == results["pdf-to-documents"]["proposed"] + results["photos-to-library"]["proposed"]
    for p in props.values():
        body = p["body"]
        assert body["action"] == "deliver"
        assert body["item_id"] and body["part_id"] and len(body["sha256"]) == 64
        assert body["destination_id"] in {"dst_documents", "dst_photos"}
        assert body["destination_checksum"]["algorithm"] in {"sha256", "sha1-base64"}
    names = {p["body"]["file_name"] for p in props.values()}
    assert {"receipt-small.pdf", "invoice-nested.pdf"} <= names
    assert "statement-2026-03.pdf" not in names and "photo-2.png" not in names
    assert "logo.png" not in names  # inline image, not an attachment


# 11. A dismissed match is not proposed again after rerunning the scan
def test_11_dismissals_stick(ws):
    ws.full_pipeline()
    target = sorted(proposals(ws))[0]
    decisions.dismiss(ws.config, target, "not needed")
    ws.scan_all()
    assert target not in proposals(ws)
    assert target in proposals(ws, "dismissed")


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
