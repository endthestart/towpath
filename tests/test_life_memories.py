"""Memories, the owner's profile, the timeline and Ask: groups proposed by rule and by the model, kept as the owner's
snapshot, and questions answered only from the owner's records with every sentence cited."""

import json
import re
import sys
from contextlib import closing
from pathlib import Path

import pytest
from django.test import Client, override_settings

from towpath import decisions, life, life_ask, life_groups
from towpath.mail import senders
from towpath.stores import open_store
from towpath.unified import grants
from towpath.unified.claims import Citation, Claim, When
from towpath.web import auth
from towpath.web.application import configure

sys.path.insert(0, str(Path(__file__).parent))
from test_mail_threads import World, answer as thread_answer  # noqa: E402

OWNER = "correct horse battery staple"


def respond(prompt: str) -> str:
    if "see their life as memories" in prompt:
        ids = re.findall(r"^- (clm_\w+) \|", prompt, re.M)
        trip = [i for i in ids if i in TRIP]
        return json.dumps({"groups": [{"kind": "trip", "title": "Trip to Example Junction", "members": trip,
                                       "summary": "Four days by the canal."}] if len(trip) >= 2 else []})
    if "Answer the owner's question" in prompt:
        return json.dumps({"unknown": False, "sentences": [
            {"text": "You booked Example Junction in May 2009.", "sources": [1]},
            {"text": "It rained the whole time.", "sources": []}]})
    return thread_answer(prompt)


TRIP: set[str] = set()


def claim(statement, day, ref, significance="notable", modality="occurred") -> Claim:
    when = When(day, "day", day, day)
    return Claim(life.claim_id(statement, modality, [ref]), "event", statement, modality, when,
                 (Citation(ref=ref, captured_excerpt=statement),), "test", significance=significance)


@pytest.fixture
def world(tmp_path, openai_stub):
    w = World(tmp_path, openai_stub)
    w.model_stub.respond = respond
    senders.categorize_pending(w.worker())
    with closing(open_store(w.store, "source", "web")) as src:
        sam = src.execute("SELECT native_id FROM items WHERE subject = 'Saturday?'").fetchone()[0]
    made = [claim("Booked a trip to Example Junction", "2009-05-01", f"{w.sid}:{sam}"),
            claim("Flew to Example Junction", "2009-05-14", f"{w.sid}:{sam}x"),
            claim("Stayed at the canal hotel", "2009-05-15", f"{w.sid}:{sam}y"),
            claim("Helped Sam move", "2011-03-03", f"{w.sid}:{sam}"),
            claim("Dinner with Sam", "2011-04-02", f"{w.sid}:{sam}"),
            claim("Started a new job", "2015-09-01", f"{w.sid}:z", "milestone")]
    TRIP.clear()
    TRIP.update(c.claim_id for c in made[:3])
    with closing(open_store(w.store, "life", "worker")) as db:
        for c in made:
            life.save(db, c, w.sid, "run_test")
        db.commit()
    yield w
    w.server.__exit__(None, None, None)


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


def test_the_pages(world, monkeypatch):
    life_groups.set_profile(world.store, "Reader", "1980-06-01")
    life_groups.propose_groups(world.worker())
    monkeypatch.setattr(auth, "_setup_codes", {})
    configure(world.store)
    auth.create_account(world.store, "owner", OWNER)
    with override_settings(TOWPATH_STORE_DIR=world.store, TOWPATH_CONFIG=world.config, ALLOWED_HOSTS=["testserver"]):
        client = Client()
        client.post("/login", {"username": "owner", "password": OWNER})
        page = client.get("/life/timeline").content.decode()
        assert "Reader's timeline" in page and "age 35" in page and "Trip to Example Junction" in page
        page = client.get("/life/memories/").content.decode()
        assert "With Sam" in page and "To review" in page
        gid = next(g["group_id"] for g in life_groups.groups(world.store) if g["kind"] == "trip")
        client.post("/life/memories/decide", {"group_id": gid, "action": "keep", "title": "Junction 2009"})
        assert "Junction 2009" in client.get(f"/life/memories/{gid}/").content.decode()
        client.post("/life/ask", {"question": "When did I go to Example Junction?"})
        assert "Looking through your records" in client.get("/life/ask").content.decode()
        client.post("/life/profile", {"name": "R", "birthday": "not a date"})
        assert life_groups.profile(world.store)["name"] == "Reader"
