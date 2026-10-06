"""Regression cases found during the local review, using synthetic sources only."""

import json
import sys
from contextlib import closing
from pathlib import Path

import pytest

from towpath import connect
from towpath.discovery import policy
from towpath.stores import open_store
from towpath.unified import collections, requests
from towpath.unified.contracts import Filters

sys.path.insert(0, str(Path(__file__).parent))
from unified_env import Uni  # noqa: E402


@pytest.fixture
def uni(tmp_path, monkeypatch):
    instance = Uni(tmp_path, monkeypatch)
    try:
        yield instance
    finally:
        instance.close()


def test_reaccepting_query_preserves_resolving_unverified_members(uni):
    collection = collections.create(uni.config.store_dir, "Photos", "query", Filters.parse("extension:nef"))
    first = collections.evaluate(uni.adapters(False), collection)
    assert collections.accept(uni.config.store_dir, collection["collection_id"], first) == 5
    accepted = collections.get(uni.config.store_dir, collection["collection_id"])
    before = {item["ref"] for item in accepted["baseline"]}
    second = collections.evaluate(uni.adapters(False), accepted)
    assert second["counts"]["unverified"] == 1
    collections.accept(uni.config.store_dir, collection["collection_id"], second)
    after = collections.get(uni.config.store_dir, collection["collection_id"])
    assert {item["ref"] for item in after["baseline"]} == before


def test_accepting_partial_query_preserves_unavailable_baseline(uni):
    collection = collections.create(uni.config.store_dir, "Photos", "query", Filters.parse("extension:nef"))
    collections.accept(uni.config.store_dir, collection["collection_id"],
                       collections.evaluate(uni.adapters(False), collection))
    accepted = collections.get(uni.config.store_dir, collection["collection_id"])
    before = {item["ref"]: item["version"] for item in accepted["baseline"]}
    policy.revoke(uni.config, "photos", "search")
    partial = collections.evaluate(uni.adapters(False), accepted)
    assert partial["partial"] and partial["counts"]["unavailable"] == 3
    collections.accept(uni.config.store_dir, collection["collection_id"], partial)
    after = collections.get(uni.config.store_dir, collection["collection_id"])
    assert {item["ref"]: item["version"] for item in after["baseline"]} == before


def test_absent_mail_part_does_not_serve_cached_content(uni):
    native = "Archive/2008;UIDVALIDITY=1700000002;UID=2#part=1"
    requests.request_part(uni.config.store_dir, "imap-fixture", native)
    assert connect.fetch_requests(uni.config)["fetched"] == 1
    assert requests.part_text(uni.config.store_dir, "imap-fixture", native)["text"] is not None
    del uni.state.mailboxes["Archive/2008"].messages[2]
    assert connect.sync(uni.config, "imap-fixture")["absent"] == 1
    assert requests.part_state(uni.config.store_dir, "imap-fixture", native)["state"] == "unavailable"
    assert requests.part_text(uni.config.store_dir, "imap-fixture", native)["text"] is None


def test_mail_labels_use_run_sequence_across_identifier_width_change(uni):
    adapter = uni.adapters(False)["gmail-fixture"]
    with closing(open_store(uni.config.store_dir, "source", "connect")) as db:
        item = db.execute("SELECT * FROM items WHERE source_id='gmail-fixture' ORDER BY item_id LIMIT 1").fetchone()
        for run_id, labels in (("run_9999", ["INBOX"]), ("run_10000", ["SENT"])):
            db.execute("INSERT INTO runs (run_id, source_id, kind, started_at) VALUES (?, 'gmail-fixture', "
                       "'incremental', '2026-10-05T12:00:00Z')", (run_id,))
            db.execute("INSERT INTO observations VALUES (?,?,?,?)", (item["item_id"], run_id,
                       "2026-10-05T12:00:00Z", json.dumps(labels)))
        db.commit()
        assert connect._latest_labels(db, item["item_id"]) == ["SENT"]
    assert adapter.describe(item["native_id"])["result"]["locator"]["labels"] == ["SENT"]


def test_describing_mail_part_preserves_selected_identity_and_metadata(uni):
    result = next(r for r in uni.search("extension:nef", False)["results"] if r["source_id"] == "imap-fixture")
    described = uni.adapters(False)["imap-fixture"].describe(result["ref"].split(":", 1)[1])
    for key in ("ref", "kind", "title", "media_type", "extension"):
        assert described["result"][key] == result[key]
    assert described["result"]["locator"]["part_id"] == "2"
