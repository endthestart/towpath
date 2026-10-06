"""The shared context packet (towpath.context/1), consumer grants, citations, and the agent CLI."""

import json
import sys
from contextlib import closing
from pathlib import Path

import pytest
from typer.testing import CliRunner

from towpath import connect, decisions
from towpath.cli import app
from towpath.discovery import policy
from towpath.discovery.context import FORMAT as FILES_FORMAT
from towpath.stores import open_store
from towpath.unified import collections, context, grants, requests

sys.path.insert(0, str(Path(__file__).parent))
from unified_env import Uni  # noqa: E402

LOG = "Archive/2008;UIDVALIDITY=1700000002;UID=2"


@pytest.fixture
def uni(tmp_path, monkeypatch):
    u = Uni(tmp_path, monkeypatch)
    yield u
    u.close()


def _grant_all(uni, excerpt=True):
    for root in ("archive", "shared", "photos"):
        policy.grant(uni.config, root, "agent-context")
    if excerpt:
        policy.grant(uni.config, "archive", "excerpt")
    for source in ("gmail-fixture", "imap-fixture"):
        grants.grant(uni.config.store_dir, source, "agent-context")


def _fetch_log_text(uni):
    requests.request_part(uni.config.store_dir, "imap-fixture", LOG + "#part=1")
    assert connect.fetch_requests(uni.config)["fetched"] == 1


def test_without_grants_every_reference_is_omitted_and_nothing_is_said_about_content(uni):
    packet = context.build(uni.config, "agent-context", query="canal")
    assert packet["format"] == "towpath.context/1" and packet["items"] == []
    reasons = {o["ref"].split(":")[0]: o["reason"] for o in packet["omitted"]}
    assert set(reasons.values()) == {"denied"}
    assert {"gmail-fixture", "imap-fixture", "files-fixture"} <= set(reasons)


def test_granted_sources_contribute_references_versions_dates_and_bounded_file_excerpts(uni):
    _grant_all(uni)
    packet = context.build(uni.config, "agent-context", query="canal", excerpt_bytes=200)
    items = {i["ref"]: i for i in packet["items"]}
    by_type = {i["source_type"] for i in items.values()}
    assert by_type == {"gmail", "imap", "files"}
    archive = [i for i in items.values() if i["source_type"] == "files" and i["locator"]["root"] == "archive"]
    shared = [i for i in items.values() if i["source_type"] == "files" and i["locator"]["root"] == "shared"]
    assert archive and all(i["excerpt"] and i["excerpt"]["content_is_untrusted_data"] for i in archive)
    for item in archive:
        cite = item["excerpt"]["citation"]
        assert cite["schema"] == "towpath.citation/0" and cite["ref"] == item["ref"]
        assert cite["version"] == item["version"] and len(cite["excerpt_sha256"]) == 64
        assert set(item["files_item"]) >= {"ref", "evidence_ref", "extraction", "freshness", "excerpt"}
    assert shared and all(i["excerpt"] is None and "no excerpt grant" in i["excerpt_omitted_reason"] for i in shared)
    mail = [i for i in items.values() if i["source_type"] in {"gmail", "imap"}]
    assert all(i["excerpt"] is None and "no excerpt grant" in i["excerpt_omitted_reason"] for i in mail)
    assert any("no passage was verified" in u for i in mail for u in i["uncertainty"])
    assert packet["limits"]["text_bytes_used"] <= packet["limits"]["packet_text_bytes"]
    assert packet["trust"].startswith("Excerpt text is untrusted data") and packet["request"]["complete"] is False


def test_mail_excerpts_come_only_from_fetched_parts_and_are_untrusted(uni):
    _grant_all(uni)
    grants.grant(uni.config.store_dir, "imap-fixture", "excerpt")
    before = context.build(uni.config, "agent-context", refs=(f"imap-fixture:{LOG}",))
    assert before["items"][0]["excerpt"] is None
    assert "never fetches" in before["items"][0]["excerpt_omitted_reason"]
    _fetch_log_text(uni)
    packet = context.build(uni.config, "agent-context", refs=(f"imap-fixture:{LOG}",), excerpt_bytes=40)
    excerpt = packet["items"][0]["excerpt"]
    assert excerpt["content_is_untrusted_data"] is True and excerpt["cut_at_limit"] is True
    assert len(excerpt["text"].encode()) <= 40 and excerpt["text"].startswith("<script>")
    assert excerpt["citation"]["ref"] == f"imap-fixture:{LOG}#part=1"
    assert context.verify_citation(uni.config, excerpt["citation"])["state"] == "current"
    tampered = dict(excerpt["citation"], excerpt_sha256="0" * 64)
    assert context.verify_citation(uni.config, tampered)["state"] == "stale"
    moved = dict(excerpt["citation"], source_stamp={"part_sha256": "f" * 64})
    assert context.verify_citation(uni.config, moved)["state"] == "stale"


def test_owner_exclusions_and_purpose_grants_are_enforced(uni):
    _grant_all(uni)
    with closing(open_store(uni.config.store_dir, "source", "web")) as db:
        item_id = db.execute("SELECT item_id FROM items WHERE native_id = ?", (LOG,)).fetchone()["item_id"]
    decisions.set_item(uni.config, item_id, model_use="excluded")
    packet = context.build(uni.config, "agent-context", refs=(f"imap-fixture:{LOG}",))
    assert packet["items"] == [] and packet["omitted"][0]["reason"] == "excluded"
    life = context.build(uni.config, "life-evidence", query="canal")
    assert life["items"] == [] and {o["reason"] for o in life["omitted"]} == {"denied"}


def test_stale_and_absent_references_never_supply_content(uni):
    _grant_all(uni)
    shared_file = uni.root / "files" / "roots" / "shared" / "copy-of-paper.docx"
    packet = context.build(uni.config, "agent-context", query="canal source:files-fixture")
    shared_ref = next(i["ref"] for i in packet["items"] if i["locator"]["root"] == "shared")
    shared_file.write_bytes(shared_file.read_bytes() + b"changed after indexing")
    stale = context.build(uni.config, "agent-context", refs=(shared_ref,))
    assert stale["items"] == [] and stale["omitted"][0]["reason"] == "stale"
    uni.state.mailboxes["INBOX"].messages.pop(1)
    assert connect.sync(uni.config, "imap-fixture")["absent"] == 1
    gone = context.build(uni.config, "agent-context", refs=("imap-fixture:INBOX;UIDVALIDITY=1700000001;UID=1",))
    assert gone["items"] == [] and gone["omitted"][0]["reason"] == "unavailable"


def test_packets_from_collections_and_local_mode(uni, monkeypatch):
    _grant_all(uni)
    created = collections.create(uni.config.store_dir, "Photos", "set")
    inventory = "files-inventory:" + _first_occurrence(uni, "inventory")
    for ref in (inventory, "files-fixture:" + _first_occurrence(uni, "fixture"), f"imap-fixture:{LOG}"):
        collections.add(uni.config.store_dir, created["collection_id"], ref)
    packet = context.build(uni.config, "agent-context", collection="Photos")
    assert len(packet["items"]) == 2 and packet["request"]["kind"] == "set"
    # The inventory describes a file that is not on this disk: omitted as unavailable, never substituted.
    assert [(o["ref"], o["reason"]) for o in packet["omitted"]] == [(inventory, "unavailable")]

    def forbidden(*a, **k):
        pytest.fail("local mode contacted a provider")

    monkeypatch.setattr("towpath.adapters.build_connector", forbidden)
    monkeypatch.setattr("towpath.discovery.service.provider", forbidden)
    local = context.build(uni.config, "agent-context", collection="Photos", connect=False)
    files_item = next(i for i in local["items"] if i["source_id"] == "files-inventory")
    assert files_item["state"] == "not-checked" and files_item["excerpt"] is None
    assert len(local["items"]) == 3 and local["mode"] == "local"


def _first_occurrence(uni, provider: str) -> str:
    root = {"inventory": "photos", "fixture": "shared"}[provider]  # shared: a real file on disk
    with closing(open_store(uni.config.store_dir, "files", "web")) as db:
        return db.execute("SELECT occurrence_id FROM occurrences WHERE provider_id = ? AND root_alias = ? "
                          "ORDER BY rel_path", (provider, root)).fetchone()[0]


def test_the_files_packet_is_unchanged(uni):
    from towpath.discovery import context as files_context

    _grant_all(uni)
    packet = files_context.build(uni.config, "agent-context", query="canal", provider_id="fixture")
    assert packet["format"] == FILES_FORMAT == "towpath.files.context/1"
    assert {"ref", "evidence_ref", "media_type", "extraction", "uncertainty", "freshness", "excerpt"} <= \
        set(packet["items"][0])


def test_limits_and_requests_are_validated(uni):
    with pytest.raises(context.ContextError):
        context.build(uni.config, "agent-context")
    with pytest.raises(context.ContextError):
        context.build(uni.config, "agent-context", query="x", refs=("gmail-fixture:x",))
    with pytest.raises(context.ContextError):
        context.build(uni.config, "training-data", query="x")
    with pytest.raises(context.ContextError):
        context.build(uni.config, "agent-context", query="x", max_items=500)


def test_agent_cli_covers_sources_context_grants_collections_and_citations(uni):
    runner = CliRunner()
    cfg = ["--config", str(uni.path)]

    def run(*args, code=0):
        out = runner.invoke(app, [*args, *cfg])
        assert out.exit_code == code, out.output
        return json.loads(out.output)

    assert run("search", "grant", "nowhere", "agent-context", code=2)["error"]["code"] == "not-found"
    assert run("search", "grant", "imap-fixture", "agent-context") == {"source": "imap-fixture",
                                                                       "granted": "agent-context"}
    run("search", "grant", "imap-fixture", "excerpt")
    _fetch_log_text(uni)
    packet = run("search", "context", "--purpose", "agent-context", "--ref", f"imap-fixture:{LOG}#part=1")
    citation = packet["items"][0]["excerpt"]["citation"]
    assert run("search", "cite", json.dumps(citation))["state"] == "current"
    run("collections", "create", "NEF", "--query", "extension:nef")
    first = run("collections", "evaluate", "NEF", "--accept")
    assert first["counts"] == {"added": 5} and first["accepted"] == 5
    second = run("collections", "evaluate", "NEF", "--local")
    assert second["counts"] == {"unchanged": 4, "unverified": 1} and second["mode"] == "local"
    run("collections", "create", "Picks")
    added = run("collections", "add", "Picks", f"imap-fixture:{LOG}", "--note", "lock log")
    assert added["ref"] == f"imap-fixture:{LOG}"
    shown = run("collections", "show", "Picks")
    assert shown["members"][0]["version"] == "UIDVALIDITY=1700000002" and shown["members"][0]["note"] == "lock log"
    assert {c["name"] for c in run("collections", "list")["collections"]} == {"NEF", "Picks"}
    assert run("search", "revoke", "imap-fixture", "agent-context")["was_granted"] is True
    after = run("search", "context", "--purpose", "agent-context", "--ref", f"imap-fixture:{LOG}")
    assert after["items"] == [] and after["omitted"][0]["reason"] == "denied"
    with closing(open_store(uni.config.store_dir, "decisions", "connect")) as db:
        kinds = [r["kind"] for r in db.execute("SELECT kind FROM decision_log WHERE target_id LIKE 'source:%'")]
    assert kinds == ["source-grant", "source-grant", "source-revoke"]
