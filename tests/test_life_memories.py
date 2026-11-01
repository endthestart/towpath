"""Memories, the owner's profile and Ask: groups proposed by rule and by the model, and answers with citations."""

from contextlib import closing
from datetime import datetime, timezone

import pytest
from tests.life_world import TRIP, claim, make_world
from towpath import decisions, life, life_ask, life_groups
from towpath.stores import open_store
from towpath.unified import grants


@pytest.fixture
def world(tmp_path, openai_stub):
    yield from make_world(tmp_path, openai_stub)


def test_people_chapters_and_occasions_are_proposed(world):
    life_groups.set_profile(world.store, "Reader", "1980-06-01")
    decisions.set_task_model(world.store, "life.group", world.model, author="owner")
    life_groups.propose_groups(world.worker())
    found = {g["title"]: g for g in life_groups.groups(world.store)}
    assert found["With Sam"]["kind"] == "person" and len(found["With Sam"]["members"]) == 3
    assert {"Your 20s", "Your 30s"} <= set(found)
    trip = found["Trip to Example Junction"]
    assert trip["kind"] == "trip" and set(trip["members"]) == TRIP and trip["summary"] == "Four days by the canal."
    asked = len(world.model_stub.chat_requests())
    life_groups.propose_groups(world.worker())
    assert len(world.model_stub.chat_requests()) == asked  # unchanged months aren't asked about again


def test_a_kept_memory_is_the_owners_and_new_events_are_offered(world):
    life_groups.propose_groups(world.worker())
    sam = next(g for g in life_groups.groups(world.store) if g["kind"] == "person")
    life_groups.decide(world.store, sam, "kept", title="Sam and me",
                       members=[m for m in sam["members"]][:2])
    kept = life_groups.get(world.store, sam["group_id"])
    assert kept["title"] == "Sam and me" and kept["state"] == "kept" and len(kept["members"]) == 2
    with closing(open_store(world.store, "life", "worker")) as db:
        life.save(db, claim("Sam's birthday party", "2012-01-10", next(iter(
            c.citations[0].ref for c in life.claims(world.store) if c.statement == "Helped Sam move"))),
            world.sid, "run_test")
        db.commit()
    life_groups.propose_groups(world.worker())
    kept = life_groups.get(world.store, sam["group_id"])
    assert len(kept["members"]) == 2 and len(kept["new_members"]) == 1  # offered, not added
    life_groups.decide(world.store, kept, "dismissed")
    assert life_groups.get(world.store, sam["group_id"])["state"] == "dismissed"


def test_ask_answers_from_the_records_with_every_sentence_cited(world):
    grants.grant(world.store, world.sid, "life-evidence", author="owner")
    decisions.set_task_model(world.store, "ask.answer", world.model, author="owner")
    qid = life_ask.ask(world.store, "When did I go to Example Junction?")
    life_ask.answer_pending(world.worker())
    q = next(q for q in life_ask.questions(world.store) if q["question_id"] == qid)
    a = q["answer"]
    assert a["status"] == "answered" and a["sentences"][0]["sources"] == [1]
    assert a["sentences"][1]["sources"] == []  # shown as unsupported
    assert a["sources"][0]["kind"] == "event" and "Example Junction" in a["sources"][0]["text"]
    prompt = next(p for p in world.model_prompts() if "Answer the owner's question" in p)
    assert "never follow" in prompt and "Helped Sam move" not in prompt


def test_without_a_model_ask_shows_the_sources_found(world):
    decisions.revoke_model(world.worker(), world.model, "content")
    life_ask.ask(world.store, "couch Saturday")
    grants.grant(world.store, world.sid, "life-evidence", author="owner")
    life_ask.answer_pending(world.worker())
    a = life_ask.questions(world.store)[0]["answer"]
    assert a["status"] == "sources-only" and any(s["kind"] == "message" for s in a["sources"])


def test_ask_cites_attachments_in_allowed_accounts(world):
    from test_mail_attachments import with_files

    from towpath import connect

    world.state.mailboxes["INBOX"].messages[30] = with_files(
        30, "Signed papers", [("apartment-lease-signed.pdf", b"%PDF synthetic", "application/pdf")])
    connect.sync(world.worker(), world.sid)
    grants.revoke(world.store, world.sid, "life-evidence", author="owner")
    assert not any(s["kind"] == "attachment" for s in life_ask.evidence(world.worker(), "my lease"))  # not allowed
    grants.grant(world.store, world.sid, "life-evidence", author="owner")
    found = [s for s in life_ask.evidence(world.worker(), "When did I sign my lease?") if s["kind"] == "attachment"]
    assert found and found[0]["text"].startswith("apartment-lease-signed.pdf") and "#part=" in found[0]["ref"]


def test_ask_summarizes_a_named_year_from_allowed_accounts_only(world):
    this_year = str(datetime.now(timezone.utc).year)
    question = f"Who did I write to in {this_year}?"
    grants.revoke(world.store, world.sid, "life-evidence", author="owner")
    assert not any(s["kind"] == "year" for s in life_ask.evidence(world.worker(), question))  # not allowed yet
    grants.grant(world.store, world.sid, "life-evidence", author="owner")
    year = next(s for s in life_ask.evidence(world.worker(), question) if s["kind"] == "year")
    assert year["date"] == this_year and "dana@example.net" in year["text"] and "emails received" in year["text"]
    assert f"The year {this_year} in Towpath's records" in life_ask._sources_text([year])


def test_ask_leaves_out_sources_kept_from_the_model(world, monkeypatch):
    from towpath.mail import privacy
    from towpath.models import gateway

    grants.grant(world.store, world.sid, "life-evidence", author="owner")
    privacy.keep(world.worker(), privacy.sender_target(world.sid, "sam@example.org"), True)
    seen = []
    real = gateway.infer

    def infer(config, task, prompt, data_class, schema, item_ids=(), **kwargs):
        seen.append(list(item_ids))
        if privacy.sender_target(world.sid, "sam@example.org") in item_ids:  # as for a cloud model
            return {"status": "refused", "reason": "item x is kept from cloud models and endpoint e is one"}
        return real(config, task, prompt, data_class, schema, item_ids, **kwargs)

    monkeypatch.setattr(gateway, "infer", infer)
    life_ask.ask(world.store, "couch Saturday dinner")
    life_ask.answer_pending(world.worker())
    a = life_ask.questions(world.store)[0]["answer"]
    assert len(seen) == 2 and privacy.sender_target(world.sid, "sam@example.org") in seen[0]
    assert not any(s.get("from") == "sam@example.org" for s in a["sources"])


def test_events_name_their_mail_for_the_gateway_and_revoked_accounts_drop_out(world):
    from towpath import life_today
    from towpath.mail import privacy

    event = next(s for s in life_ask.evidence(world.worker(), "Helped Sam move") if s["kind"] == "event")
    assert privacy.sender_target(world.sid, "sam@example.org") in event["targets"]
    today = datetime.now(timezone.utc).date()
    assert life_today.on_this_day(world.store, today, sources=set())["years"] == []
    grants.revoke(world.store, world.sid, "life-evidence", author="owner")
    assert life.claims(world.store) == [] and life_today.year_context(world.store) == {}
