"""File discovery on the synthetic corpus with the fixture provider (a test aid)."""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import Files
from towpath import config as config_mod
from towpath.discovery import corpus, policy, service
from towpath.discovery.providers.base import Unavailable

REPO = Path(__file__).resolve().parents[1]
PAPER_SHA = hashlib.sha256(corpus.docx(corpus.PAPER_TEXT)).hexdigest()


def test_file_commands_need_no_mail_models_or_life(fx):
    assert not fx.config.sources and not fx.config.endpoints and not fx.config.tasks
    assert service.status(fx.config)["providers"]["fixture"]["adapter"] == "fixture"
    assert service.probe(fx.config)[0]["available"] is True
    with pytest.raises(policy.Denied):
        fx.search("zebrafinch")
    fx.grant("archive", "search")
    assert fx.search("zebrafinch")["results"]


def test_disabled_or_absent_discovery(fx, tmp_path):
    fx.reload('recover_dir = "derived/recovered"', 'enabled = false\nrecover_dir = "derived/recovered"')
    with pytest.raises(service.Disabled):
        fx.search("zebrafinch")
    bare = tmp_path / "bare.toml"
    bare.write_text("[stores]\ndir = 'state'\n")
    cfg = config_mod.load(bare)
    assert service.status(cfg) == {"configured": False}
    with pytest.raises(service.Disabled):
        service.search(cfg, "x")


def test_no_provider_configured_is_honest(fx):
    text = fx.path.read_text()
    fx.path.write_text(text[:text.index("[[files.providers]]")])
    fx.reload()
    assert service.status(fx.config)["providers"] == {}
    assert service.probe(fx.config) == []
    with pytest.raises(Unavailable, match="no files provider"):
        fx.search("zebrafinch")


def test_zip_mbox_attachment_lineage_is_complete(fx):
    fx.grant("archive", "search")
    hit = fx.find("zebrafinch economy", "message 1 > paper.docx")
    assert hit["locator"]["root"] == "archive" and hit["locator"]["path"] == "2003/old-mail.zip"
    assert [(m["kind"], m["name"], m["index"]) for m in hit["locator"]["members"]] == [
        ("archive-member", "mail/backup.mbox", None), ("mail-message", None, 1), ("attachment", "paper.docx", 0)]
    assert hit["locator"]["native_id"] == "fx-att-1"
    assert {d["meaning"] for d in hit["dates"]} == {"message-date", "member-modified"}
    assert hit["hashes"] == {"sha256": PAPER_SHA}
    assert hit["passage"] == {"kind": "text-offset", "start": corpus.PAPER_TEXT.lower().index("zebrafinch")}
    assert hit["media_type"] == corpus.DOCX_TYPE and hit["version"].startswith("size=")


def test_search_never_leaks_excluded_ungranted_or_escaped_content(fx):
    fx.grant("archive", "search")
    result = fx.search("zebrafinch")
    blob = json.dumps(result)
    locations = [r["location"] for r in result["results"]]
    assert not any("private/" in loc or "escape-link" in loc or loc.startswith("shared:") for loc in locations)
    for secret in ("Private diary", "outside the root", "IGNORE ALL PREVIOUS", "towpath tolls"):
        assert secret not in blob
    assert all("text" not in r and "snippet" not in r for r in result["results"])
    for query in ("diary", "outside the root"):
        empty = fx.search(query)
        assert empty["results"] == [] and empty["more_may_exist"] is False
        assert set(empty) == set(result)


def test_duplicate_bytes_stay_distinct_occurrences(fx):
    fx.grant("archive", "search")
    fx.grant("shared", "search")
    papers = [r for r in fx.search("zebrafinch economy")["results"] if r["hashes"].get("sha256") == PAPER_SHA]
    assert len(papers) == 3
    assert len({r["occurrence_id"] for r in papers}) == 3
    assert {r["locator"]["root"] for r in papers} == {"archive", "shared"}


def test_statuses_and_truncation_are_reported(fx):
    fx.grant("archive", "search")
    report, = service.import_catalog(fx.config)
    assert report["complete"] and report["absence_established"]
    assert report["by_status"] == {"indexed": 9, "truncated": 1, "encrypted": 1, "failed": 1, "unsupported": 1}
    assert report["refused_references"] == 1  # the symlink that points outside the root
    # The only line mentioning a kingfisher lies beyond the provider's text limit, so it cannot be found.
    assert fx.search("kingfisher")["results"] == []
    long = fx.find("lock keeper", "long/thesis-notes.txt")
    assert long["extraction"]["status"] == "truncated" and long["extraction"]["truncated"] is True
    assert long["extraction"]["limit_bytes"] == corpus.TEXT_LIMIT


def test_cli_end_to_end(tmp_path):
    out = tmp_path / "corpus"
    env = dict(os.environ, PYTHONPATH=str(REPO / "src"))
    subprocess.run([sys.executable, "-m", "towpath", "fixtures", "files", str(out)], check=True, env=env,
                   capture_output=True)
    fx = Files.__new__(Files)
    fx.root, fx.path = out, out / "towpath.toml"
    denied = fx.cli("files", "search", "zebrafinch", check=False)
    assert denied.returncode == 2 and "error (denied)" in denied.stderr and "Traceback" not in denied.stderr
    fx.cli("files", "grant", "archive", "search")
    fx.cli("files", "grant", "archive", "excerpt")
    found = json.loads(fx.cli("files", "search", "zebrafinch economy", "--limit", "1").stdout)
    occ = found["results"][0]["occurrence_id"]
    assert json.loads(fx.cli("files", "describe", occ).stdout)["state"] == "current"
    ex = json.loads(fx.cli("files", "excerpt", occ, "--max-bytes", "20").stdout)
    assert len(ex["text"].encode()) <= 20
    assert json.loads(fx.cli("files", "cite", ex["citation"]["citation_id"]).stdout)["state"] == "current"
    missing = fx.cli("files", "recover", occ, check=False)
    assert missing.returncode == 2 and "error (denied)" in missing.stderr
    status = json.loads(fx.cli("files", "status").stdout)
    assert status["roots"]["archive"]["grants"] == ["excerpt", "search"]
    bad = fx.cli("files", "grant", "archive", "root-shell", check=False)
    assert bad.returncode == 2 and "feature must be one of" in bad.stderr


def test_discovery_config_leaves_email_pipeline_working(ws):
    ws.add_config('[files]\nenabled = false\n[[files.roots]]\nalias = "a"\npath = "nowhere"\n')
    first = ws.full_pipeline()
    assert first and not (ws.config.store_dir / "files.db").exists()
    ws.replace_config("enabled = false", "enabled = true")
    assert ws.full_pipeline().keys() == first.keys()
    assert not (ws.config.store_dir / "files.db").exists()
