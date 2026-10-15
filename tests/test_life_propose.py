"""Proposing life events: only when the owner allowed the account and the model, only from likely messages, and
only events whose quote appears in the message. A stand-in IMAP server and model; invented mail only."""

import json
import sys
from pathlib import Path

import pytest

from towpath import config as config_mod, connections, decisions, life, life_propose, request_worker
from towpath.unified import grants

sys.path.insert(0, str(Path(__file__).parent))
import imap_stub as stub  # noqa: E402
from test_connections import PASSWORD, WRITES  # noqa: E402

BOOKING = "Your booking for Example Junction, 14-17 May 2009, is confirmed."
ORDER = "Thank you for your order of a red bicycle."


def mailboxes():
    return {"INBOX": stub.Mailbox(1700000001, {
        1: stub.Message(stub.message(1, "Your booking is confirmed", f"{BOOKING} Reference ABC123.",
                                     date="Thu, 02 Apr 2009 10:15:00 +0000")),
        2: stub.Message(stub.message(2, "Weekly deals just for you", "Save 20% on everything this week.",
                                     date="Fri, 03 Apr 2009 09:00:00 +0000")),
        3: stub.Message(stub.message(3, "Your order receipt", f"{ORDER} It ships tomorrow.",
                                     date="Sat, 04 Apr 2009 08:00:00 +0000"))})}


def event(statement, modality, earliest, latest, precision, excerpt, significance="notable"):
    return {"statement": statement, "significance": significance, "modality": modality, "kind": "event",
            "expression": earliest, "earliest": earliest, "latest": latest, "precision": precision, "excerpt": excerpt}


def answer(*events, email="transaction"):
    return json.dumps({"email": email, "events": list(events)})


def allow(config, sid, mid):
    life_propose.set_request(config.store_dir, sid, "2009-01-01", "2009-12-31")
    grants.grant(config.store_dir, sid, "life-evidence")
    decisions.grant_model(connections.merged(config, None, role="web"), mid, "content")
    return connections.merged(config, config.store_dir.parent / "credentials")


def email_prompts(model):
    return [r["body"]["messages"][-1]["content"] for r in model.chat_requests()
            if "Email date:" in r["body"]["messages"][-1]["content"]]


@pytest.fixture
def world(tmp_path, openai_stub):
    state = stub.State(mailboxes(), password=PASSWORD)
    server = stub.Server(state).__enter__()
    (tmp_path / "towpath.toml").write_text('[stores]\ndir = "state"\n')
    config = config_mod.load(tmp_path / "towpath.toml")
    credentials = tmp_path / "credentials"
    settings = {"host": "127.0.0.1", "port": server.port, "security": "plain-loopback", "username": state.username}
    sid = connections.add_imap(config.store_dir, credentials, "imap", settings, PASSWORD,
                               connections.test_imap(settings, PASSWORD))
    connections.choose_folders(config.store_dir, sid, ["INBOX"])
    connections.start_indexing(config.store_dir, credentials, sid)
    request_worker.run_once(config, credentials)  # index the mailbox's metadata
    model = openai_stub(model="local-9b", answers=[
        answer(event("Ordered a red bicycle", "occurred", "2009-04-04", "2009-04-04", "day", ORDER, "routine")),
        answer(event("Planned a trip to Example Junction", "plan", "2009-05-14", "2009-05-17", "range", BOOKING),
               event("Won a prize", "occurred", "2009-04-02", "2009-04-02", "day", "You have won a prize"))])
    form = {"base_url": f"http://127.0.0.1:{model.port}/v1", "model": "local-9b"}
    mid = connections.add_model(config.store_dir, credentials, form, "token-1", connections.test_model(form, "token-1"))
    yield config, credentials, sid, mid, model, state
    server.__exit__(None, None, None)


def test_nothing_is_read_or_sent_until_the_owner_allows_it(world):
    config, credentials, sid, mid, model, state = world
    worker = connections.merged(config, credentials)
    assert life_propose.blocked(worker, life_propose.request(config.store_dir)) == (
        "Choose a mail account and a period to start.")
    life_propose.set_request(config.store_dir, sid, "2009-01-01", "2009-12-31")
    assert "isn't allowed for your life story" in life_propose.blocked(worker, life_propose.request(config.store_dir))
    grants.grant(config.store_dir, sid, "life-evidence")
    assert "isn't allowed to read email text" in life_propose.blocked(worker, life_propose.request(config.store_dir))
    before = len(model.chat_requests())
    assert life_propose.propose_pending(worker, credentials) == [] and len(model.chat_requests()) == before
    decisions_db = config.store_dir / "decisions.db"
    written = decisions_db.stat().st_mtime_ns
    life_propose.propose_pending(worker, credentials)
    assert decisions_db.stat().st_mtime_ns == written  # the worker only reads the owner's decisions


def test_events_are_proposed_with_verbatim_evidence_and_never_twice(world):
    config, credentials, sid, mid, model, state = world
    life_propose.set_request(config.store_dir, sid, "2009-01-01", "2009-12-31")
    grants.grant(config.store_dir, sid, "life-evidence")
    decisions.grant_model(connections.merged(config, None, role="web"), mid, "content")
    worker = connections.merged(config, credentials)
    result = life_propose.propose_pending(worker, credentials)
    assert result == [{"source_id": sid, "step": "proposing", "examined": 2, "proposed_total": 2}]
    sent = email_prompts(model)
    assert len(sent) == 2 and not any("Save 20%" in s for s in sent)  # advertising is never sent to the model
    claims = {c.statement: c for c in life.claims(config.store_dir)}
    assert set(claims) == {"Ordered a red bicycle", "Planned a trip to Example Junction"}  # the prize had no quote
    trip, bike = claims["Planned a trip to Example Junction"], claims["Ordered a red bicycle"]
    assert trip.modality == "plan" and trip.when.bounds()[0].isoformat() == "2009-05-14" and not trip.when.basis
    assert trip.citations[0].captured_excerpt == BOOKING and "plan, not that it happened" in trip.uncertainty
    assert [b.meaning for b in bike.when.basis] == ["message-date"]  # dated by the receipt itself
    assert (bike.significance, trip.significance) == ("routine", "notable")
    assert trip.producer == f"model:local-9b/{life_propose.PROMPT_VERSION}"
    assert trip.citations[0].ref.startswith(f"{sid}:INBOX")
    assert life_propose.propose_pending(worker, credentials) == []  # nothing left in the slice
    assert life_propose.progress(config.store_dir)["state"] == "done"
    assert len(life.claims(config.store_dir)) == 2
    assert state.violations == [] and not any(
        line.split(" ", 1)[1].split(" ")[0].upper() in WRITES for line in state.transcript)


def test_a_paused_request_does_nothing(world):
    config, credentials, sid, mid, model, state = world
    life_propose.set_request(config.store_dir, sid, "2009-01-01", "2009-12-31")
    grants.grant(config.store_dir, sid, "life-evidence")
    decisions.grant_model(connections.merged(config, None, role="web"), mid, "content")
    life_propose.pause(config.store_dir, True)
    assert life_propose.propose_pending(connections.merged(config, credentials), credentials) == []
    with pytest.raises(ValueError):
        life_propose.set_request(config.store_dir, sid, "2010-01-01", "2009-01-01")


def test_a_mail_account_that_stops_pauses_proposing_without_stopping_the_worker(world, monkeypatch):
    from towpath.adapters import imap
    from towpath.adapters.errors import AuthStop

    config, credentials, sid, mid, model, state = world
    life_propose.set_request(config.store_dir, sid, "2009-01-01", "2009-12-31")
    grants.grant(config.store_dir, sid, "life-evidence")
    decisions.grant_model(connections.merged(config, None, role="web"), mid, "content")
    worker = connections.merged(config, credentials)
    real = imap.ImapConnector.fetch
    monkeypatch.setattr(imap.ImapConnector, "fetch", lambda *a, **k: (_ for _ in ()).throw(AuthStop("sign in again")))
    assert life_propose.propose_pending(worker, credentials) == [
        {"step": "stopped", "reason": "The mail account stopped: sign in again"}]
    run = life_propose.progress(config.store_dir)
    assert run["state"] == "stopped" and run["examined"] == 0 and run["retry_at"] > run["stopped_at"]
    assert life_propose.propose_pending(worker, credentials) == []  # waits for the retry time
    assert request_worker.run_once(config, credentials)["termination"] == "complete"  # the worker carries on
    monkeypatch.setattr(imap.ImapConnector, "fetch", real)
    life_propose.pause(config.store_dir, False)  # the owner presses Try again now
    assert life_propose.propose_pending(worker, credentials)[0]["proposed_total"] == 2
    assert life_propose.progress(config.store_dir)["state"] == "running"


def test_an_unreachable_model_leaves_messages_unread_for_later(world):
    config, credentials, sid, mid, model, state = world
    life_propose.set_request(config.store_dir, sid, "2009-01-01", "2009-12-31")
    grants.grant(config.store_dir, sid, "life-evidence")
    decisions.grant_model(connections.merged(config, None, role="web"), mid, "content")
    model.close()  # the model server is gone
    worker = connections.merged(config, credentials)
    assert life_propose.propose_pending(worker, credentials) == [
        {"step": "stopped", "reason": "Towpath's worker couldn't reach the AI model."}]
    assert life_propose.progress(config.store_dir)["examined"] == 0


def test_promotions_marketing_and_spam_never_become_events(world):
    from towpath.stores import open_store

    config, credentials, sid, mid, model, state = world
    with open_store(config.store_dir, "source", "connect") as db:  # the provider filed the receipt as Promotions
        db.execute("UPDATE observations SET labels = '[\"CATEGORY_PROMOTIONS\", \"INBOX\"]' WHERE item_id IN "
                   "(SELECT item_id FROM items WHERE subject = 'Your order receipt')")
    model.answers[:] = [answer(event("Planned a trip to Example Junction", "plan", "2009-05-14", "2009-05-17",
                                     "range", BOOKING), email="spam")]
    worker = allow(config, sid, mid)
    life_propose.propose_pending(worker, credentials)
    assert [p for p in email_prompts(model) if ORDER in p] == []  # never sent to the model
    assert len(email_prompts(model)) == 1 and life.claims(config.store_dir) == []
    with open_store(config.store_dir, "life", life.WRITER) as db:
        assert sorted(r[0] for r in db.execute("SELECT outcome FROM examined")) == ["advertising", "ok"]


def test_a_new_prompt_reads_again_but_leaves_what_the_owner_decided(world, monkeypatch):
    config, credentials, sid, mid, model, state = world
    worker = allow(config, sid, mid)
    life_propose.propose_pending(worker, credentials)
    first = {c.statement: c for c in life.claims(config.store_dir)}
    life.review(config.store_dir, first["Ordered a red bicycle"].claim_id, "reject")
    monkeypatch.setattr(life_propose, "PROMPT_VERSION", "life.propose/next")
    model.answers[:] = [answer(event("Booked a stay at Example Junction", "plan", "2009-05-14", "2009-05-17",
                                     "range", BOOKING))]
    before = len(email_prompts(model))
    life_propose.propose_pending(worker, credentials)
    assert len(email_prompts(model)) == before + 1  # the rejected receipt isn't read again
    now = {c.statement: c.state for c in life.claims(config.store_dir)}
    assert now == {"Ordered a red bicycle": "rejected", "Booked a stay at Example Junction": "proposed"}


def test_a_busy_model_is_waited_for_and_nothing_is_marked_read(world, monkeypatch):
    from towpath.models import gateway

    config, credentials, sid, mid, model, state = world
    worker = allow(config, sid, mid)
    busy = {"status": "manual-review", "reason": "json_schema: ConflictError; prompt: ConflictError"}
    monkeypatch.setattr(gateway, "infer", lambda *a, **k: busy)  # Poundlock answers 409 while serving someone else
    assert life_propose.propose_pending(worker, credentials) == [
        {"step": "stopped", "reason": "The AI model is busy with another request; Towpath tries again in a minute."}]
    run = life_propose.progress(config.store_dir)
    assert run["examined"] == 0 and run["state"] == "stopped"
