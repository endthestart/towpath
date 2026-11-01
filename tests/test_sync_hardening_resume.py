"""Gmail sync hardening: clean stops, recorded coverage, and resuming correctly."""

import json

import pytest

from tests.gmail_pacing import fail_get, http_error, target
from tests.gmail_fake import Call
from towpath import connect, fieldmask
from towpath.fixtures import generator
from towpath.quota import BudgetLock


def test_ctrl_c_while_waiting_is_recorded_and_releases_everything(paced):
    paced.clock.cancel_on_sleep = 4
    with pytest.raises(KeyboardInterrupt):
        connect.sync(paced.config, "src_a")
    db = paced.db("source")
    run = dict(db.execute("SELECT * FROM runs ORDER BY seq DESC LIMIT 1").fetchone())
    cov = db.execute("SELECT termination FROM coverage WHERE run_id = ?", (run["run_id"],)).fetchone()
    state = dict(db.execute("SELECT * FROM sync_state WHERE source_id = 'src_a'").fetchone())
    db.close()
    assert run["termination"] == "cancelled" and run["complete"] == 0 and cov[0] == "cancelled"
    assert state["full_sync_history_id"] and state["cursor"] is None
    assert BudgetLock._held == {}
    paced.clock.cancel_on_sleep = None
    assert connect.sync(paced.config, "src_a")["complete"]


def test_unexpected_error_is_recorded_sanitized_and_reraised(paced, monkeypatch):
    from towpath.adapters.gmail import GmailConnector
    original, calls = GmailConnector._record, {"n": 0}

    def flaky(self, msg):
        calls["n"] += 1
        if calls["n"] == 3:
            raise ValueError("synthetic failure mentioning person@example.com")
        return original(self, msg)

    monkeypatch.setattr(GmailConnector, "_record", flaky)
    with pytest.raises(ValueError):
        connect.sync(paced.config, "src_a")
    db = paced.db("source")
    run = dict(db.execute("SELECT * FROM runs ORDER BY seq DESC LIMIT 1").fetchone())
    db.close()
    assert run["termination"] == "error" and "ValueError" in run["reason"] and "@" not in run["reason"]
    assert BudgetLock._held == {}


def test_capped_run_is_never_complete_and_keeps_checkpoint(paced):
    result = connect.sync(paced.config, "src_a", max_items=7)
    assert result["termination"] == "capped" and not result["complete"]
    db = paced.db("source")
    state = dict(db.execute("SELECT * FROM sync_state WHERE source_id = 'src_a'").fetchone())
    db.close()
    assert state["full_sync_history_id"] == paced.service.data["historyId"] and state["cursor"] is None
    again = connect.sync(paced.config, "src_a")
    assert again["complete"] and again["indexed_total"] == paced.summary["messages_a"]


# -- resume efficiency and correctness ----------------------------------------------

def test_resume_starts_at_the_saved_page_not_the_first(paced, monkeypatch):
    mid = target(paced, 25)  # on the third page of ten
    fail_get(paced, monkeypatch, {mid: [http_error(429, "rateLimitExceeded") for _ in range(6)]})
    stopped = connect.sync(paced.config, "src_a")
    assert stopped["termination"] == "quota-stop"
    paced.service.calls.clear()
    resumed = connect.sync(paced.config, "src_a")
    assert resumed["complete"] and resumed["indexed_total"] == paced.summary["messages_a"]
    first_list = next(kw for name, kw in paced.service.calls if name == "messages.list")
    assert first_list["pageToken"] == "20"
    db = paced.db("source")
    assert db.execute("SELECT COUNT(*) FROM items").fetchone()[0] == paced.summary["messages_a"]
    assert db.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == paced.summary["messages_a"]
    db.close()


def test_invalid_saved_page_token_restarts_listing_without_duplicates(paced, monkeypatch):
    connect.sync(paced.config, "src_a", max_items=15)
    original, stale = paced.service.list, {"pending": True}

    def strict_list(**kw):
        if kw.get("pageToken") == "10" and stale.pop("pending", False):  # only the saved token is stale
            paced.service.calls.append(("messages.list", kw))

            def reject():
                raise http_error(400, "invalidArgument")
            return Call(reject)
        return original(**kw)

    monkeypatch.setattr(paced.service, "list", strict_list)
    resumed = connect.sync(paced.config, "src_a")
    assert resumed["complete"] and resumed["indexed_total"] == paced.summary["messages_a"]
    reads = [kw["id"] for name, kw in paced.service.calls if name == "messages.get"]
    assert len(reads) == len(set(reads))


def test_mailbox_shift_during_scan_is_caught_by_reconcile(paced, monkeypatch):
    ids = sorted(paced.service.data["messages"], reverse=True)
    deleted, shifted = ids[3], ids[10]
    original, state = paced.service.list, {"n": 0}

    def shifting_list(**kw):
        state["n"] += 1
        page = original(**kw).execute()
        if state["n"] == 1:
            del paced.service.data["messages"][deleted]  # offsets shift: page two now skips `shifted`
        return Call(lambda: page)

    monkeypatch.setattr(paced.service, "list", shifting_list)
    result = connect.sync(paced.config, "src_a")
    assert result["complete"] and result["indexed_total"] == paced.summary["messages_a"] - 1
    db = paced.db("source")
    indexed = {r[0] for r in db.execute("SELECT native_id FROM items WHERE absent_since_run IS NULL")}
    db.close()
    assert shifted in indexed and deleted not in indexed


def test_partial_listing_is_not_evidence_of_absence(paced, monkeypatch):
    connect.sync(paced.config, "src_a")
    generator.expire_cursor(paced.root)
    paced.service.data = json.loads((paced.root / "account_a.json").read_text())
    hidden = target(paced, 5)
    original, state = paced.service.list, {"n": 0}

    def flaky_list(**kw):
        state["n"] += 1
        page = original(**kw).execute()
        if state["n"] > 4:  # the reconcile pass omits a message that still exists
            page = dict(page, messages=[m for m in page["messages"] if m["id"] != hidden])
        return Call(lambda: page)

    monkeypatch.setattr(paced.service, "list", flaky_list)
    result = connect.sync(paced.config, "src_a")
    assert result["complete"] and result["absent"] == 0
    reads = [kw["id"] for name, kw in paced.service.calls if name == "messages.get"]
    assert reads.count(hidden) >= 2  # read during the rescan and once more to confirm


def test_incremental_checkpoints_never_pass_unprocessed_events(paced, monkeypatch):
    connect.sync(paced.config, "src_a")
    summary = paced.advance()
    paced.service.data = json.loads((paced.root / "account_a.json").read_text())
    history = paced.service.data["history"]
    added_record = next(h for h in history if "messagesAdded" in h)
    earlier = [h["id"] for h in history if int(h["id"]) < int(added_record["id"])]
    fail_get(paced, monkeypatch, {summary["added"][0]: [http_error(429, "rateLimitExceeded") for _ in range(6)]})
    stopped = connect.sync(paced.config, "src_a")
    assert stopped["termination"] == "quota-stop" and stopped["kind"] == "incremental"
    db = paced.db("source")
    cursor = db.execute("SELECT cursor FROM sync_state WHERE source_id = 'src_a'").fetchone()[0]
    db.close()
    assert cursor == max(earlier, key=int)  # the last record fully processed, never past the stopped one
    resumed = connect.sync(paced.config, "src_a")
    assert resumed["complete"] and resumed["cursor"] == paced.service.data["historyId"]
    db = paced.db("source")
    for native in summary["relabeled"]:
        rows = db.execute("SELECT labels FROM observations WHERE item_id = ? ORDER BY run_id",
                          (connect.item_id("src_a", native),)).fetchall()
        assert json.loads(rows[-1][0]) == ["CATEGORY_PROMOTIONS", "Label_Newsletters"]
    assert db.execute("SELECT absent_since_run IS NOT NULL FROM items WHERE native_id = ?",
                      (summary["deleted"],)).fetchone()[0] == 1
    db.close()


def test_sync_never_fetches_content_and_masks_exclude_body_data(paced):
    connect.sync(paced.config, "src_a")
    generator.expire_cursor(paced.root)
    paced.service.data = json.loads((paced.root / "account_a.json").read_text())
    connect.sync(paced.config, "src_a")
    assert not [c for c in paced.service.calls if c[0] == "attachments.get"]
    masks = {kw["fields"] for name, kw in paced.service.calls if name == "messages.get"}
    assert masks == {fieldmask.message_mask()} and "data" not in fieldmask.message_mask()
