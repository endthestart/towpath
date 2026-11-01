"""Acceptance checks for the first slice (docs/first-slice.md), numbered as there."""

import json
from pathlib import Path

import pytest

from towpath import connect, decisions, scan
from towpath.adapters import FixtureGmailClient, GmailConnector
from towpath.fixtures import generator

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
