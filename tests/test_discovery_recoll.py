"""Recoll adapter against a stub of Recoll's Python binding: records, grants, recovery, and failures."""

import json
from pathlib import Path

import pytest

from tests.recoll_stub import PAPER_SHA
from towpath import config as config_mod
from towpath.discovery import corpus, service
from towpath.discovery.providers.base import Unavailable

def test_probe_reports_binding_and_capabilities(rw):
    probe, = service.probe(rw.config)
    assert probe["available"] and probe["binding"] is True and probe["tool"] == "recoll"
    assert probe["capabilities"]["recover"] == "verified"
    assert probe["capabilities"]["hashes"] == "not-provided"


def test_recoll_rows_become_full_lineage_records(rw):
    rw.grant("archive", "search")
    result = service.search(rw.config, "zebrafinch economy")
    paper = [r for r in result["results"] if r["location"].endswith("paper.docx")]
    assert [r["location"] for r in paper] == [
        "archive:2003/old-mail.zip > mail/backup.mbox > message 1 > paper.docx",
        "archive:2003/old-mail.zip > mail/backup.mbox > message 2 > paper.docx"]
    first = paper[0]
    assert [(m["kind"], m["name"], m["index"]) for m in first["locator"]["members"]] == [
        ("archive-member", "mail/backup.mbox", None), ("mail-message", None, 1), ("attachment", "paper.docx", 0)]
    assert first["locator"]["native_id"].endswith("|mail/backup.mbox:1:0")
    dates = {d["meaning"]: d["value"] for d in first["dates"]}
    assert dates["message-date"] == "2003-05-05T10:00:00Z"
    assert first["version"].startswith("recoll-sig=") and first["hashes"] == {} and first["size"] is None
    assert first["extraction"]["status"] == "indexed" and first["extraction"]["truncated"] is None


def test_broad_recoll_index_cannot_leak(rw):
    rw.grant("archive", "search")
    (rw.conf / "fake-ignore-dir").write_text("")
    (rw.conf / "fake-no-xapian").write_text("")  # listing by record ID never leaves the folder; query paging can
    result = service.search(rw.config, "zebrafinch")
    assert result["results"]
    blob = json.dumps(result)
    for leaked in ("example.com", "outside", "diary", "escape-link", "shared:", "Private"):
        assert leaked not in blob
    report, = service.import_catalog(rw.config, root="archive")
    assert report["refused_references"] >= 3 and "inode/directory" not in json.dumps(report)


def test_excerpt_recover_and_staleness_through_recoll(rw):
    rw.grant("archive", "search", "excerpt", "recover")
    paper = service.search(rw.config, "zebrafinch economy")["results"][0]
    ex = service.excerpt(rw.config, paper["occurrence_id"], max_bytes=200)
    assert ex["text"] == corpus.PAPER_TEXT[:200] and "<p>" not in ex["text"]
    assert ex["source_extraction_truncated"] is None
    rec = service.recover(rw.config, paper["occurrence_id"])
    assert rec["sha256"] == PAPER_SHA and rec["matches_provider_hash"] is None
    assert Path(rec["recovered_to"]).name == "paper.docx"
    evil = [r for r in service.search(rw.config, "traversal")["results"]][0]
    assert Path(service.recover(rw.config, evil["occurrence_id"])["recovered_to"]).name == "evil.txt"

    cid = ex["citation"]["citation_id"]
    for row in rw.index:
        if row.get("ipath") == "mail/backup.mbox:1:0":
            row["sig"] = "999"
    rw.save()
    assert service.resolve_citation(rw.config, cid)["state"] == "stale"
    with pytest.raises(service.Stale):
        service.excerpt(rw.config, paper["occurrence_id"])
    rw.index = [r for r in rw.index if r.get("ipath") != "mail/backup.mbox:1:0"]
    rw.save()
    assert service.describe(rw.config, paper["occurrence_id"])["state"] == "unavailable"


def test_top_level_recovery_copies_inside_the_root_only(rw):
    rw.grant("archive", "search", "recover")
    note = [r for r in service.search(rw.config, "admin mode")["results"]][0]
    rec = service.recover(rw.config, note["occurrence_id"])
    assert Path(rec["recovered_to"]).read_text() == corpus.INJECTION_TEXT


def test_missing_binding_timeout_and_output_cap_are_unavailable(rw, monkeypatch, tmp_path):
    rw.grant("archive", "search")
    broken = tmp_path / "broken" / "recoll"
    broken.mkdir(parents=True)
    (broken / "__init__.py").write_text("raise ImportError('no binding')\n")
    monkeypatch.setenv("PYTHONPATH", str(broken.parent))
    with pytest.raises(Unavailable, match="binding is not importable"):
        service.search(rw.config, "zebrafinch")
    assert service.probe(rw.config)[0]["available"] is False

    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "stub"))
    (rw.conf / "fake-sleep").write_text("5")
    rw.reload("[files.limits]\ntimeout_seconds = 1")
    with pytest.raises(Unavailable, match="timeout"):
        service.search(rw.config, "zebrafinch")
    (rw.conf / "fake-sleep").unlink()
    rw.path.write_text(rw.path.read_text().replace("timeout_seconds = 1", "max_output_bytes = 200"))
    rw.config = config_mod.load(rw.path)
    with pytest.raises(Unavailable, match="output-limit"):
        service.search(rw.config, "zebrafinch")
    runs = service.status(rw.config)
    assert runs["configured"]


def test_source_change_without_reindex_is_not_current(rw):
    rw.grant("archive", "search", "excerpt", "recover")
    paper = service.search(rw.config, "zebrafinch economy")["results"][0]
    source = rw.root / "roots/archive/2003/old-mail.zip"
    source.write_bytes(source.read_bytes() + b"\nsynthetic source modification\n")
    assert service.describe(rw.config, paper["occurrence_id"])["state"] == "changed"
    with pytest.raises(service.Stale):
        service.excerpt(rw.config, paper["occurrence_id"])
    with pytest.raises(service.Stale):
        service.recover(rw.config, paper["occurrence_id"])


def test_raw_row_cap_with_directory_does_not_mark_missing(rw):
    rw.grant("archive", "search")
    initial = service.import_catalog(rw.config, root="archive")[0]
    assert initial["complete"] and initial["occurrences_seen"] == 7
    folder = rw.root / "roots/archive/2003"
    rw.index.insert(0, {
        "url": f"file://{folder}", "ipath": "", "mtype": "inode/directory",
        "rcludi": f"{folder}|", "text": "",
    })
    rw.save()
    capped = service.import_catalog(rw.config, root="archive", max_items=1)[0]
    assert capped["complete"] is False
    assert capped["absence_established"] is False
    assert capped["marked_missing"] == 0
