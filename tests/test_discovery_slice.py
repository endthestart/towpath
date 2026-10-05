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
from towpath.discovery import corpus, policy, refs, service
from towpath.discovery.providers.base import Hit, Listing, Unavailable
from towpath.discovery.records import Extraction
from towpath.stores import open_store

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


def test_excerpt_needs_its_own_grant_and_is_bounded(fx):
    fx.grant("archive", "search")
    paper = fx.find("zebrafinch economy", "message 1 > paper.docx")
    with pytest.raises(policy.Denied):
        service.excerpt(fx.config, paper["occurrence_id"])
    fx.grant("archive", "excerpt")
    ex = service.excerpt(fx.config, paper["occurrence_id"], start=paper["passage"]["start"], max_bytes=30)
    assert ex["text"] == corpus.PAPER_TEXT[paper["passage"]["start"]:][:30]
    assert ex["excerpt_cut_at_limit"] is False and ex["content_is_untrusted_data"] is True
    assert ex["citation"]["version"] == paper["version"]
    with pytest.raises(service.DiscoveryError):
        service.excerpt(fx.config, paper["occurrence_id"], start=-1)
    long = fx.find("lock keeper", "long/thesis-notes.txt")
    big = service.excerpt(fx.config, long["occurrence_id"], max_bytes=10**6)
    assert len(big["text"].encode()) == fx.config.files.limits["max_excerpt_bytes"]
    assert big["source_extraction_truncated"] is True
    assert not policy.allowed(fx.config, "archive", "agent-context")
    assert not policy.allowed(fx.config, "archive", "life-evidence")


def test_stale_citations_never_serve_different_content(fx):
    fx.grant("archive", "search", "excerpt")
    paper = fx.find("zebrafinch economy", "message 1 > paper.docx")
    ex = service.excerpt(fx.config, paper["occurrence_id"], start=paper["passage"]["start"])
    cid = ex["citation"]["citation_id"]
    assert service.resolve_citation(fx.config, cid)["state"] == "current"
    assert service.resolve_citation(fx.config, cid)["text_verified"] is True

    container = fx.root / "roots" / "archive" / "2003" / "old-mail.zip"
    container.write_bytes(container.read_bytes() + b"changed")
    assert service.resolve_citation(fx.config, cid)["state"] == "stale"
    assert service.describe(fx.config, paper["occurrence_id"])["state"] == "changed"
    with pytest.raises(service.Stale):
        service.excerpt(fx.config, paper["occurrence_id"])

    container.unlink()
    assert service.resolve_citation(fx.config, cid)["state"] in {"unavailable", "stale"}
    with pytest.raises(Unavailable):
        service.excerpt(fx.config, paper["occurrence_id"])


def test_recovery_writes_a_separate_copy_with_provenance(fx):
    fx.grant("archive", "search")
    paper = fx.find("zebrafinch economy", "message 1 > paper.docx")
    with pytest.raises(policy.Denied):
        service.recover(fx.config, paper["occurrence_id"])
    fx.grant("archive", "recover")
    rec = service.recover(fx.config, paper["occurrence_id"])
    dest = Path(rec["recovered_to"])
    assert dest.name == "paper.docx" and hashlib.sha256(dest.read_bytes()).hexdigest() == PAPER_SHA
    assert rec["matches_provider_hash"] is True and rec["members"][-1]["name"] == "paper.docx"
    assert fx.config.files.recover_dir in dest.parents
    assert not any(Path(r.path) in dest.parents for r in fx.config.files.roots.values())
    assert json.loads((dest.parent / "paper.docx.provenance.json").read_text())["sha256"] == PAPER_SHA
    assert service.recover(fx.config, paper["occurrence_id"])["recovered_to"] == str(dest)
    archive_files = sorted(p.name for p in (fx.root / "roots" / "archive" / "2003").iterdir())
    assert archive_files == ["old-mail.zip"]

    evil = fx.find("traversal member", "../../evil.txt")
    out = Path(service.recover(fx.config, evil["occurrence_id"])["recovered_to"])
    assert out.name == "evil.txt" and fx.config.files.recover_dir in out.parents
    absolute = fx.find("absolute member", "/abs.txt")
    with pytest.raises(Unavailable):
        service.recover(fx.config, absolute["occurrence_id"])


def test_recover_dir_symlinked_into_a_root_is_refused(fx):
    fx.grant("archive", "search", "recover")
    paper = fx.find("zebrafinch economy", "message 1 > paper.docx")
    recover_dir = fx.config.files.recover_dir
    recover_dir.parent.mkdir(parents=True, exist_ok=True)
    recover_dir.symlink_to(fx.root / "roots" / "archive" / "notes")
    with pytest.raises(refs.BadReference):
        service.recover(fx.config, paper["occurrence_id"])


def test_prompt_injection_is_returned_as_inert_data(fx):
    fx.grant("archive", "search", "excerpt")
    before_log = fx.decision_log()
    before_files = sorted(str(p) for p in fx.root.rglob("*"))
    note = fx.find("zebrafinch", "notes/injection.md")
    ex = service.excerpt(fx.config, note["occurrence_id"])
    assert ex["text"] == corpus.INJECTION_TEXT and ex["content_is_untrusted_data"] is True
    assert fx.decision_log() == before_log
    assert policy.granted(fx.config) == {"archive": {"search", "excerpt"}}
    after_files = sorted(str(p) for p in fx.root.rglob("*"))
    state = str(fx.config.store_dir)
    assert [p for p in after_files if p not in before_files and not p.startswith(state)] == []


def test_malicious_references_are_refused(fx):
    roots = fx.config.files.roots
    base = str(roots["archive"].path)
    for bad in ("http://example.com/paper.docx", "ftp://example.com/x", "file://evil.example/etc/passwd",
                "/etc/passwd", f"{base}/../../outside/secret.txt", f"file://{base}/escape-link",
                f"{base}/notes\x00/injection.md", "relative/path.txt", "javascript:alert(1)"):
        with pytest.raises(refs.BadReference):
            refs.locate(bad, roots)
    assert refs.locate(f"file://{base}/notes/injection.md", roots) == ("archive", "notes/injection.md")
    assert refs.locate(f"file://{base}/notes/inj%65ction.md", roots) == ("archive", "notes/injection.md")
    for bad in ("../x", "/abs", "a/../../b", "a\\..\\b", ""):
        with pytest.raises(refs.BadReference):
            refs.check_relative(bad)
    assert refs.safe_filename("../../evil.txt") == "evil.txt"
    assert refs.safe_filename("/abs/..") == "recovered"
    fx.grant("archive", "search")
    with pytest.raises(service.NotFound):
        service.describe(fx.config, "../../etc/passwd")


def test_hostile_provider_rows_are_dropped(fx, monkeypatch):
    fx.grant("archive", "search")
    base = str(fx.config.files.roots["archive"].path)
    rows = ["http://example.com/x", f"{base}/../../outside/secret.txt", f"{base}/private/diary.txt",
            f"file://{base}/escape-link", f"{fx.root}/roots/shared/copy-of-paper.docx", f"{base}/notes/injection.md"]

    def hostile(self, query, roots, max_rows, timeout):
        return Listing([Hit(f"h{n}", url, (), Extraction("indexed")) for n, url in enumerate(rows)], True, len(rows))

    from towpath.discovery.providers import fixture
    monkeypatch.setattr(fixture.Provider, "search", hostile)
    result = fx.search("anything")
    assert [r["location"] for r in result["results"]] == ["archive:notes/injection.md"]


def test_limits_cap_pages_and_report_more(fx):
    fx.grant("archive", "search")
    page = fx.search("zebrafinch", limit=2)
    assert len(page["results"]) == 2 and page["more_may_exist"] is True
    rest = fx.search("zebrafinch", limit=2, offset=2)
    assert {r["occurrence_id"] for r in rest["results"]}.isdisjoint(r["occurrence_id"] for r in page["results"])
    assert fx.search("zebrafinch", limit=10**6)["limit"] == fx.config.files.limits["max_results"]
    with pytest.raises(service.DiscoveryError):
        fx.search("zebrafinch", offset=10**6)
    with pytest.raises(service.DiscoveryError):
        fx.search("   ")
    fx.reload('recover_dir = "derived/recovered"', 'recover_dir = "derived/recovered"\n[files.limits]\nmax_scan = 2')
    capped = fx.search("zebrafinch")
    assert len(capped["results"]) <= 2 and capped["more_may_exist"] is True


def test_imports_are_repeatable_and_honest_about_absence(fx, monkeypatch):
    fx.grant("archive", "search", "excerpt")
    first, = service.import_catalog(fx.config)
    again, = service.import_catalog(fx.config)
    assert again["occurrences_seen"] == first["occurrences_seen"] and again["changed"] == 0
    count = lambda: open_store(fx.config.store_dir, "files", "worker").execute(  # noqa: E731
        "SELECT count(*), sum(missing_since_run IS NOT NULL) FROM occurrences").fetchone()
    assert tuple(count()) == (first["occurrences_seen"], 0)

    partial, = service.import_catalog(fx.config, max_items=2)
    assert partial["termination"] == "partial" and partial["absence_established"] is False
    assert partial["marked_missing"] == 0

    from towpath.discovery.providers import fixture
    original = fixture.Provider.enumerate

    def interrupted(self, root, max_rows, timeout):
        listing = original(self, root, max_rows, timeout)

        def hits():
            for n, hit in enumerate(listing.hits):
                if n == 3:
                    raise KeyboardInterrupt
                yield hit

        return Listing(hits(), listing.exhausted, listing.raw_rows)

    monkeypatch.setattr(fixture.Provider, "enumerate", interrupted)
    with pytest.raises(KeyboardInterrupt):
        service.import_catalog(fx.config)
    monkeypatch.setattr(fixture.Provider, "enumerate", original)
    runs = open_store(fx.config.store_dir, "files", "worker").execute(
        "SELECT termination FROM runs WHERE kind = 'import' ORDER BY rowid").fetchall()
    assert [r[0] for r in runs] == ["complete", "complete", "partial", "interrupted"]
    assert tuple(count()) == (first["occurrences_seen"], 0)

    catalog = json.loads((fx.root / "catalog.json").read_text())
    catalog["entries"] = [e for e in catalog["entries"] if e["native_id"] != "fx-pst"]
    (fx.root / "catalog.json").write_text(json.dumps(catalog))
    gone, = service.import_catalog(fx.config)
    assert gone["complete"] and gone["marked_missing"] == 1


def test_rebuilding_files_db_keeps_ids_grants_and_citations(fx):
    fx.grant("archive", "search", "excerpt")
    paper = fx.find("zebrafinch economy", "message 1 > paper.docx")
    citation = service.excerpt(fx.config, paper["occurrence_id"], start=5, max_bytes=40)["citation"]
    (fx.config.store_dir / "files.db").unlink()
    service.import_catalog(fx.config)
    assert policy.granted(fx.config) == {"archive": {"search", "excerpt"}}
    rebuilt = fx.find("zebrafinch economy", "message 1 > paper.docx")
    assert rebuilt["occurrence_id"] == paper["occurrence_id"]
    with pytest.raises(service.NotFound):
        service.resolve_citation(fx.config, citation["citation_id"])  # the citation log was rebuilt away
    resolved = service.resolve_citation(fx.config, citation)  # a citation carried elsewhere still resolves
    assert resolved["state"] == "current" and resolved["text_verified"] is True


def test_revoked_grant_hides_results_again(fx):
    fx.grant("archive", "search")
    occ = fx.find("zebrafinch economy", "message 1 > paper.docx")["occurrence_id"]
    policy.revoke(fx.config, "archive", "search")
    with pytest.raises(policy.Denied):
        service.describe(fx.config, occ)
    with pytest.raises(ValueError):
        policy.grant(fx.config, "archive", "everything")
    with pytest.raises(ValueError):
        policy.grant(fx.config, "nowhere", "search")


def test_exclusion_added_later_hides_recorded_occurrences(fx):
    fx.grant("archive", "search")
    occ = fx.find("zebrafinch", "notes/injection.md")["occurrence_id"]
    fx.reload('exclude = ["private/**"]', 'exclude = ["private/**", "notes/*"]')
    with pytest.raises(service.NotFound):
        service.describe(fx.config, occ)
    assert "notes/injection.md" not in json.dumps(fx.search("zebrafinch"))


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


def test_fixture_index_goes_stale_until_reindexed(fx):
    fx.grant("archive", "search", "excerpt")
    note = fx.find("zebrafinch", "notes/injection.md")
    path = fx.root / "roots" / "archive" / "notes" / "injection.md"
    path.write_text(path.read_text() + "appended later\n")
    described = service.describe(fx.config, note["occurrence_id"])
    assert (described["state"], described["provider_version"], described["source"]) == ("changed", "same", "changed")
    with pytest.raises(service.Stale):
        service.excerpt(fx.config, note["occurrence_id"])
    assert corpus.reindex(fx.root)["dropped"] == 0
    assert service.describe(fx.config, note["occurrence_id"])["provider_version"] == "changed"
    again = fx.find("zebrafinch", "notes/injection.md")
    assert again["source"]["state"] == "fresh" and again["version"] != note["version"]
    assert service.excerpt(fx.config, note["occurrence_id"])["state"] == "current"
    path.unlink()
    assert corpus.reindex(fx.root)["dropped"] == 1
    assert service.describe(fx.config, note["occurrence_id"])["state"] == "unavailable"


def test_fixture_listing_boundaries(fx):
    fx.grant("archive", "search")
    raw = sum(1 for e in json.loads((fx.root / "catalog.json").read_text())["entries"] if e["root"] == "archive")
    exact, = service.import_catalog(fx.config, max_items=raw)
    assert exact["complete"] and exact["absence_established"]
    short, = service.import_catalog(fx.config, max_items=raw - 1)
    assert short["complete"] is False and short["marked_missing"] == 0
    page = fx.search("zebrafinch", limit=200)
    assert page["more_may_exist"] is False

