"""File discovery: the catalog through imports, rebuilds and grants, and listing boundaries."""

import json
from pathlib import Path

import pytest

from towpath.discovery import corpus, policy, refs, service
from towpath.discovery.providers.base import Listing
from towpath.stores import open_store


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



def test_the_listing_fast_path_accepts_and_refuses_exactly_what_the_full_check_does(fx):
    from pathlib import PurePosixPath

    def slow(rel):
        if not rel or "\x00" in rel:
            return "refused"
        pure = PurePosixPath(rel)
        if pure.is_absolute() or ".." in pure.parts or "\\" in rel:
            return "refused"
        return str(pure)

    def fast(rel):
        try:
            return refs.check_relative(rel)
        except refs.BadReference:
            return "refused"
    tricky = ["a", "a/b.txt", "a//b", "./a", "a/./b", "a/", "a/.", ".", "..", "../a", "a/..", "a/../b", "a\\b",
              "a/..b", "...", "a/.hidden", ".hidden", "/a", "a\x00b", "", " a/b ", "a/b/c/d.e"]
    assert [fast(r) for r in tricky] == [slow(r) for r in tricky]
    roots = fx.config.files.roots
    archive = Path(roots["archive"].path)
    folders = refs.Folders()
    for rel in ("notes/injection.md", "escape-link", "long/thesis-notes.txt", "notes/../escape-link"):
        try:
            full = refs.resolve_in_root(roots["archive"], rel)
            expected = ("accepted", str(full))
        except refs.BadReference:
            expected = ("refused", None)
        try:
            refs._inside(roots["archive"], rel, folders)
            got = ("accepted", folders.real_text(f"{folders.base_text(roots['archive'].path)}/{rel}"))
        except refs.BadReference:
            got = ("refused", None)
        assert got == expected, rel
    assert archive.exists()


def test_a_link_named_in_another_case_is_still_followed(fx, tmp_path):
    probe = tmp_path / "CaseProbe"
    probe.write_text("x")
    if not (tmp_path / "caseprobe").exists():
        pytest.skip("this filesystem tells names apart by case")
    roots = fx.config.files.roots
    archive = Path(roots["archive"].path)
    (archive / "esc").symlink_to(fx.root / "outside")
    for name in ("Esc", "ESC", "esc"):
        with pytest.raises(refs.BadReference):
            refs.locate(f"file://{archive}/{name}/secret.txt", roots, refs.Folders())
