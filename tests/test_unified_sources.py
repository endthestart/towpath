"""Unified adapters over real stores and providers: Gmail fixture, synthetic IMAP server, files and a manifest."""

import json
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from towpath import config as config_mod
from towpath import connect
from towpath.cli import app
from towpath.discovery import policy, service
from towpath.unified import environment, federation, sources
from towpath.unified.contracts import Filters, decode_cursor

sys.path.insert(0, str(Path(__file__).parent))
import imap_stub as stub  # noqa: E402


class Uni:
    def __init__(self, root: Path, monkeypatch):
        self.state = stub.State(stub.build(environment.imap_mailboxes()))
        self.server = stub.Server(self.state).__enter__()
        monkeypatch.setenv(environment.IMAP_PASSWORD_ENV, self.state.password)
        self.root = root
        environment.generate(root, imap_port=self.server.port)
        self.path = root / "towpath.toml"
        self.config = config_mod.load(self.path)
        for source_id in ("gmail-fixture", "imap-fixture"):
            assert connect.sync(self.config, source_id)["termination"] == "complete"
        for root_alias in ("archive", "shared", "photos"):
            policy.grant(self.config, root_alias, "search")
        service.import_catalog(self.config, "fixture")
        service.import_catalog(self.config, "inventory")

    def adapters(self, connect_mode: bool = True):
        return sources.build_adapters(self.config, connect=connect_mode)

    def search(self, query: str, connect_mode: bool = True, limit: int = 20, cursor=None) -> dict:
        adapters = self.adapters(connect_mode)
        try:
            return federation.search(adapters, Filters.parse(query), limit, cursor).to_dict()
        finally:
            federation.close_all(adapters)

    def close(self):
        self.server.__exit__(None, None, None)


@pytest.fixture
def uni(tmp_path, monkeypatch):
    u = Uni(tmp_path, monkeypatch)
    yield u
    u.close()


def _pages(resp: dict) -> dict:
    return {s["source_id"]: s for s in resp["sources"]}


def test_one_keyword_query_reaches_gmail_imap_and_files_with_their_own_depths(uni):
    resp = uni.search("canal")
    pages = _pages(resp)
    assert {k: (v["status"], v["depth"]) for k, v in pages.items()} == {
        "gmail-fixture": ("ok", "provider-search"), "imap-fixture": ("ok", "provider-search"),
        "files-fixture": ("ok", "content-index"), "files-inventory": ("ok", "catalog")}
    titles = {(r["source_type"], r["title"]) for r in resp["results"]}
    assert ("gmail", "Canal society newsletter") in titles
    assert ("imap", "Canal boat club minutes") in titles  # matched in the body by the server's SEARCH
    assert ("files", "copy-of-paper.docx") in titles
    assert all(r["match"]["verified_passage"] is False and r["excerpt"] is None for r in resp["results"])
    assert resp["complete"] is False
    assert "files-inventory: keyword text matched metadata only, not message or file content" in \
        resp["incomplete_because"]


def test_body_text_matches_through_provider_search_but_not_the_local_catalog(uni):
    connected = uni.search("fundraiser")  # only in an IMAP message body
    assert [r["title"] for r in connected["results"] if r["source_type"] == "imap"] == ["Canal boat club minutes"]
    local = uni.search("fundraiser", connect_mode=False)
    assert all(s["depth"] == "catalog" for s in local["sources"])
    assert [r for r in local["results"] if r["source_type"] == "imap"] == []
    assert "imap-fixture: keyword text matched metadata only, not message or file content" in \
        local["incomplete_because"]


def test_nef_query_finds_references_in_every_source_without_text_extraction(uni):
    resp = uni.search("extension:nef", connect_mode=False)
    by_source: dict = {}
    for r in resp["results"]:
        by_source.setdefault(r["source_id"], []).append(r)
    assert [r["title"] for r in by_source["gmail-fixture"]] == ["DSC_0042.NEF"]
    assert [r["title"] for r in by_source["imap-fixture"]] == ["DSC_0044.NEF"]
    inventory = {r["title"]: r for r in by_source["files-inventory"]}
    assert set(inventory) == {"DSC_0042.NEF", "DSC_0043.nef", "DCIM/DSC_0001.NEF"}
    nested = inventory["DCIM/DSC_0001.NEF"]
    assert nested["kind"] == "archive-member" and nested["locator"]["path"] == "2003/camera-backup.zip"
    assert nested["hashes"] == {"sha256": "0" * 63 + "1"}  # stated by the manifest, never computed here
    assert inventory["DSC_0042.NEF"]["hashes"] == {}  # unknown stays unknown
    assert all(r["coverage"] == "metadata-cataloged" and r["excerpt"] is None for r in inventory.values())
    assert resp["complete"] is False
    assert "files-inventory: its inventory of the declared scope is not known to be complete" in \
        resp["incomplete_because"]


def test_manifest_import_reports_rejected_entries_and_an_incomplete_scope(uni):
    report = service.import_catalog(uni.config, "inventory")[0]
    assert report["complete"] is False and report["absence_established"] is False
    assert report["occurrences_seen"] == 5  # the escaping entry was rejected by the manifest reader
    status = next(s for s in federation.statuses(uni.adapters(False)) if s["source_id"] == "files-inventory")
    assert status["coverage"]["counts"] == {"metadata-cataloged": 4, "unreadable": 1}
    assert status["coverage"]["inventory_complete"] is False and status["depths"] == ["catalog"]


def test_local_mode_never_builds_a_connector_or_starts_a_provider(uni, monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("local mode must not contact a source")

    monkeypatch.setattr("towpath.adapters.build_connector", refuse)
    monkeypatch.setattr("towpath.discovery.service.provider", refuse)
    uni.state.transcript.clear()
    resp = uni.search("canal", connect_mode=False)
    assert all(s["status"] == "ok" and s["depth"] == "catalog" for s in resp["sources"])
    statuses = federation.statuses(uni.adapters(False))
    assert {s["capabilities"].get("provider-search") for s in statuses if s["source_type"] != "files"} == \
        {"disabled"}
    assert uni.state.transcript == []


def test_an_unreachable_imap_server_does_not_erase_other_sources(uni):
    uni.close()
    resp = uni.search("canal")
    pages = _pages(resp)
    assert pages["imap-fixture"]["status"] == "unavailable"
    assert pages["imap-fixture"]["error"]["code"] == "server-stop"
    assert pages["gmail-fixture"]["status"] == "ok" and pages["files-fixture"]["status"] == "ok"
    assert any(r["source_type"] == "files" for r in resp["results"])


def test_ungranted_roots_are_disclosed_and_never_searched(uni):
    policy.revoke(uni.config, "photos", "search")
    resp = uni.search("extension:nef", connect_mode=False)
    page = _pages(resp)["files-inventory"]
    assert page["status"] == "denied" and page["result_count"] == 0
    status = next(s for s in federation.statuses(uni.adapters(False)) if s["source_id"] == "files-inventory")
    assert status["scope"]["ungranted_roots"] == ["photos"]
    assert "not searched: photos" in " ".join(status["coverage"]["notes"])


def test_excluded_paths_never_appear_in_catalog_or_content_results(uni):
    for mode in (False, True):
        resp = uni.search("diary", connect_mode=mode)
        assert not any("private/" in json.dumps(r["locator"]) for r in resp["results"])
    resp = uni.search("zebrafinch")
    assert not any("diary" in json.dumps(r) for r in resp["results"])


def test_per_source_paging_continues_only_unfinished_sources(uni):
    first = uni.search("extension:nef", connect_mode=False, limit=1)
    assert set(decode_cursor(first["next_cursor"])) == {"files-inventory"}
    second = uni.search("extension:nef", connect_mode=False, limit=1, cursor=first["next_cursor"])
    assert [s["source_id"] for s in second["sources"]] == ["files-inventory"]
    third = uni.search("extension:nef", connect_mode=False, limit=1, cursor=second["next_cursor"])
    refs = {r["ref"] for page in (first, second, third) for r in page["results"] if r["source_type"] == "files"}
    assert len(refs) == 3 and third["next_cursor"] is None


def test_gmail_provider_search_uses_the_existing_client_with_q(uni, monkeypatch):
    calls = []
    from towpath import adapters

    real = adapters.build_connector

    def spy(source, config=None, **kw):
        connector = real(source, config, **kw)
        if source.adapter == "fixture-gmail":
            calls.append(connector.client.calls)
        return connector

    monkeypatch.setattr(adapters, "build_connector", spy)
    resp = uni.search("aqueduct source:gmail-fixture")
    assert {r["title"] for r in resp["results"]} == {"Towpath survey notes", "Photos from the aqueduct walk"}
    assert ("list_messages", None, "aqueduct") in calls[0]
    page = resp["sources"][0]
    assert page["estimate"]["value"] == 2 and "estimate" in page["estimate"]["basis"]


def test_provider_matches_not_yet_indexed_are_labelled(uni):
    account = uni.root / "mail" / "account_u.json"
    data = json.loads(account.read_text())
    newest = sorted(data["messages"])[-1]
    clone = dict(data["messages"][newest], id="18d00000000000ff", threadId="18d00000000000ff")
    data["messages"]["18d00000000000ff"] = clone
    account.write_text(json.dumps(data))
    resp = uni.search("towpath source:gmail-fixture")
    unindexed = [r for r in resp["results"] if r["native"] == "18d00000000000ff"]
    assert unindexed and unindexed[0]["coverage"] == "discovered"
    assert unindexed[0]["locator"]["indexed_locally"] is False


def test_typed_filters_turn_provider_matches_into_matching_parts(uni):
    resp = uni.search("walk extension:nef")
    refs = sorted(r["ref"] for r in resp["results"] if r["source_type"] in {"gmail", "imap"})
    assert len(refs) == 2 and all("#part=" in r for r in refs)


def test_describe_inspects_each_kind_of_reference(uni):
    adapters = uni.adapters(True)
    try:
        nef = uni.search("extension:nef", connect_mode=False)
        mail_part = next(r["ref"] for r in nef["results"] if r["source_type"] == "imap")
        described = federation.describe(adapters, mail_part)
        assert described["state"] == "present" and [p["selected"] for p in described["parts"]] == [False, True]
        inventory = next(r["ref"] for r in nef["results"] if r["source_id"] == "files-inventory")
        file_view = federation.describe(adapters, inventory)
        assert file_view["state"] == "unavailable"  # the manifest's file is not on this disk: never substituted
        with pytest.raises(federation.UnknownReference):
            federation.describe(adapters, "gmail-fixture:does-not-exist")
    finally:
        federation.close_all(adapters)
    local = federation.describe(uni.adapters(False), inventory)
    assert local["state"] == "not-checked"


def test_search_commands_emit_json(uni):
    runner = CliRunner()
    out = runner.invoke(app, ["search", "sources", "--config", str(uni.path), "--local"])
    assert out.exit_code == 0, out.output
    assert [s["source_id"] for s in json.loads(out.output)["sources"]] == [
        "gmail-fixture", "imap-fixture", "files-fixture", "files-inventory"]
    out = runner.invoke(app, ["search", "query", "extension:nef", "--source", "files-inventory", "--config",
                              str(uni.path), "--local"])
    data = json.loads(out.output)
    assert [s["source_id"] for s in data["sources"]] == ["files-inventory"] and len(data["results"]) == 3
    out = runner.invoke(app, ["search", "describe", "gmail-fixture:nope", "--config", str(uni.path), "--local"])
    assert out.exit_code == 1 and json.loads(out.output)["error"]["code"] == "not-found"
