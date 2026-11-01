"""File discovery: excerpts, citations and recovery, and refusing whatever a source or reference shouldn't reach."""

import hashlib
import json
from pathlib import Path

import pytest

from tests.test_discovery_slice import PAPER_SHA
from towpath.discovery import corpus, policy, refs, service
from towpath.discovery.providers.base import Hit, Listing, Unavailable
from towpath.discovery.records import Extraction


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


def test_listing_resolves_each_folder_once_and_still_refuses_links_out(fx, monkeypatch):
    roots = fx.config.files.roots
    archive = Path(roots["archive"].path)
    (archive / "linked").symlink_to(fx.root / "outside")
    (archive / "notes" / "inside-link").symlink_to(archive / "notes" / "injection.md")
    resolved, real = [], refs._real
    monkeypatch.setattr(refs, "_real", lambda p: (resolved.append(Path(p).name), real(p))[1])
    folders = refs.Folders()
    for rel in ("notes/injection.md", "notes/inside-link", "long/thesis-notes.txt"):
        assert refs.locate(f"file://{archive}/{rel}", roots, folders) == ("archive", rel)
    for escaping in ("escape-link", "linked", "linked/secret.txt"):
        with pytest.raises(refs.BadReference):
            refs.locate(f"file://{archive}/{escaping}", roots, folders)
    assert "inside-link" in resolved and "escape-link" in resolved  # links are followed one by one
    assert not {"injection.md", "thesis-notes.txt"} & set(resolved)  # ordinary files: only their folder


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
