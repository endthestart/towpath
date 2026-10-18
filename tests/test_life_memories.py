"""Memories, the owner's profile, the timeline and Ask: groups proposed by rule and by the model, kept as the owner's
snapshot, and questions answered only from the owner's records with every sentence cited."""

import json
import re
import sys
from contextlib import closing
from datetime import datetime, timezone
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
    grants.grant(w.store, w.sid, "life-evidence", author="owner")  # events only come from allowed accounts
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
        assert 'href="/life/year/2009"' in page
        page = client.get("/life/year/2009").content.decode()
        assert "Booked a trip to Example Junction" in page and "AGE 29" in page and "2010 →" in page
        this_year = str(datetime.now(timezone.utc).year)
        page = client.get(f"/life/year/{this_year}").content.decode()
        assert "First wrote to" in page and "dana@example.net" in page and "emails received" in page
        assert client.get("/life/year/later").status_code == 404
        page = client.get("/life/memories/").content.decode()
        assert "With Sam" in page and "To review" in page
        gid = next(g["group_id"] for g in life_groups.groups(world.store) if g["kind"] == "trip")
        client.post("/life/memories/decide", {"group_id": gid, "action": "keep", "title": "Junction 2009"})
        assert "Junction 2009" in client.get(f"/life/memories/{gid}/").content.decode()
        client.post("/life/ask", {"question": "When did I go to Example Junction?"})
        assert "Looking through your records" in client.get("/life/ask").content.decode()
        home = client.get("/").content.decode()
        assert "waiting for your reply" in home and "events to review" in home and "3 in the inbox" in home
        client.post("/life/profile", {"name": "R", "birthday": "not a date"})
        assert life_groups.profile(world.store)["name"] == "Reader"


def test_on_this_day_and_each_years_context(world, monkeypatch):
    from datetime import date

    from towpath import life_today

    found = life_today.on_this_day(world.store, date(2026, 5, 14))
    assert [y["year"] for y in found["years"]] == ["2009"]
    assert [c.statement for c in found["years"][0]["events"]] == ["Flew to Example Junction"]
    context = life_today.year_context(world.store)
    assert sum(v.get("sent", 0) for v in context.values()) == 3
    assert any("dana@example.net" in v.get("people", []) for v in context.values())
    monkeypatch.setattr(auth, "_setup_codes", {})
    configure(world.store)
    auth.create_account(world.store, "owner", OWNER)
    with override_settings(TOWPATH_STORE_DIR=world.store, TOWPATH_CONFIG=world.config, ALLOWED_HOSTS=["testserver"]):
        client = Client()
        client.post("/login", {"username": "owner", "password": OWNER})
        assert "Flew to Example Junction" in client.get("/life/today?day=2026-05-14").content.decode()
        assert "emails received" in client.get("/life/timeline").content.decode()


def test_people_pages(world, monkeypatch):
    from towpath import people

    found = {p["address"]: p for p in people.everyone(world.store)}
    assert {"sam@example.org", "pat@example.org"} <= set(found) and "news@shop.example" not in found
    sam = people.person(world.store, "sam@example.org")
    assert sum(y["received"] for y in sam["years"]) == 1 and sum(y["sent"] for y in sam["years"]) == 1
    assert {c.statement for c in sam["events"]} == {"Booked a trip to Example Junction", "Helped Sam move",
                                                   "Dinner with Sam"}
    monkeypatch.setattr(auth, "_setup_codes", {})
    configure(world.store)
    auth.create_account(world.store, "owner", OWNER)
    with override_settings(TOWPATH_STORE_DIR=world.store, TOWPATH_CONFIG=world.config, ALLOWED_HOSTS=["testserver"]):
        client = Client()
        client.post("/login", {"username": "owner", "password": OWNER})
        assert "sam@example.org" in client.get("/people/?q=sam").content.decode()
        page = client.get("/people/sam@example.org/").content.decode()
        assert "Helped Sam move" in page and "Saturday?" in page
        assert "1 from them, 1 from you" in page and f"/mail/{world.sid}/search?q=from%3Asam%40example.org" in page


def test_the_owners_own_memories(world, monkeypatch):
    remembered = life.recollect(world.store, "The summer we drove to the coast", "the summer after college", "month",
                                "2002-07-01", "2002-07-31", ["Sam"])
    found = {c.claim_id: c for c in life.claims(world.store, limit=1000)}
    assert found[remembered.claim_id].modality == "recollected" and found[remembered.claim_id].state == "accepted"
    assert life.claims(world.store, before="2003-01-01")[0].claim_id == remembered.claim_id  # in date order
    assert any(s["text"] == "The summer we drove to the coast" for s in life_ask.evidence(world.worker(), "coast"))
    monkeypatch.setattr(auth, "_setup_codes", {})
    configure(world.store)
    auth.create_account(world.store, "owner", OWNER)
    with override_settings(TOWPATH_STORE_DIR=world.store, TOWPATH_CONFIG=world.config, ALLOWED_HOSTS=["testserver"]):
        client = Client()
        client.post("/login", {"username": "owner", "password": OWNER})
        assert client.post("/life/remember", {"statement": "First day at school", "date": "1986-09"}).status_code == 302
        assert client.post("/life/remember", {"statement": "x", "date": "someday"}).status_code == 400
        page = client.get("/life/timeline").content.decode()
        assert "First day at school" in page and "your memory" in page
        client.post("/life/forget", {"claim_id": remembered.claim_id})
    assert remembered.claim_id not in {c.claim_id for c in life.claims(world.store, limit=1000)}


def test_the_story_holds_only_what_the_owner_kept(world, monkeypatch):
    from towpath import life_story

    life_groups.set_profile(world.store, "Reader", "1980-06-01")
    life_groups.propose_groups(world.worker())
    trip = next(g for g in life_groups.groups(world.store) if g["kind"] == "trip")
    life_groups.decide(world.store, trip, "kept")
    job = next(c for c in life.claims(world.store) if c.statement == "Started a new job")
    life.review(world.store, job.claim_id, "accept")
    life.recollect(world.store, "Learned to swim", "1987", "year", "1987-01-01", "1987-12-31")
    found = life_story.story(world.store)
    names = [n for n, _ in found["chapters"]]
    assert names == ["Childhood", "Your 20s", "Your 30s"]
    text = life_story.markdown(found)
    assert "# Reader's story" in text and "Trip to Example Junction" in text
    assert "Learned to swim, 1987 (remembered)" in text
    assert "Helped Sam move" not in text  # proposed, never accepted
    assert "[^1]:" in text
    monkeypatch.setattr(auth, "_setup_codes", {})
    configure(world.store)
    auth.create_account(world.store, "owner", OWNER)
    with override_settings(TOWPATH_STORE_DIR=world.store, TOWPATH_CONFIG=world.config, ALLOWED_HOSTS=["testserver"]):
        client = Client()
        client.post("/login", {"username": "owner", "password": OWNER})
        assert "Learned to swim" in client.get("/life/story").content.decode()
        md = client.get("/life/story?format=md")
        assert md["Content-Disposition"].startswith("attachment") and b"Started a new job" in md.content


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


def test_the_export_package_holds_what_towpath_learned_and_no_mail_text(world, monkeypatch):
    import io
    import zipfile

    from towpath.mail import senders as mail_senders

    grants.grant(world.store, world.sid, "mail-manage", author="owner")
    mail_senders.mark(world.store, world.sid, "sam@example.org", "person")
    monkeypatch.setattr(auth, "_setup_codes", {})
    configure(world.store)
    auth.create_account(world.store, "owner", OWNER)
    with override_settings(TOWPATH_STORE_DIR=world.store, TOWPATH_CONFIG=world.config, ALLOWED_HOSTS=["testserver"]):
        client = Client()
        client.post("/login", {"username": "owner", "password": OWNER})
        response = client.get("/export")
    assert response["Content-Type"] == "application/zip" and "towpath-export-" in response["Content-Disposition"]
    z = zipfile.ZipFile(io.BytesIO(response.content))
    names = set(z.namelist())
    assert {"README.md", "life/events.json", "life/story.md", "people.json", "decisions.json",
            f"mail/{world.sid}/senders.json"} <= names
    events = json.loads(z.read("life/events.json"))
    assert "Helped Sam move" in {e["statement"] for e in events}
    sam = next(s for s in json.loads(z.read(f"mail/{world.sid}/senders.json")) if s["address"] == "sam@example.org")
    assert sam["chosen_by_you"] is True
    everything = b"".join(z.read(n) for n in names)
    from test_mail_threads import PASSWORD

    assert b"move a couch" not in everything  # no mail text
    assert PASSWORD.encode() not in everything and OWNER.encode() not in everything  # no secrets


def test_events_that_may_disagree_are_paired_for_the_owner_to_settle(world, monkeypatch):
    from towpath import life_conflicts

    with closing(open_store(world.store, "life", "worker")) as db:
        life.save(db, claim("Flew to Example Junction", "2009-05-15", f"{world.sid}:other"), world.sid, "run_b")
        life.save(db, claim("Helped Sam move", "2011-05-20", f"{world.sid}:other2"), world.sid, "run_b")
        db.commit()
    found = life_conflicts.find(world.store)
    kinds = {(p["a"].statement, p["kind"]) for p in found}
    assert ("Flew to Example Junction", "the same event twice?") in kinds
    assert ("Helped Sam move", "one event, dates that disagree?") in kinds
    assert not any("Dinner with Sam" in (p["a"].statement, p["b"].statement) for p in found)  # not alike enough
    monkeypatch.setattr(auth, "_setup_codes", {})
    configure(world.store)
    auth.create_account(world.store, "owner", OWNER)
    with override_settings(TOWPATH_STORE_DIR=world.store, TOWPATH_CONFIG=world.config, ALLOWED_HOSTS=["testserver"]):
        client = Client()
        client.post("/login", {"username": "owner", "password": OWNER})
        assert "2 pairs to settle" in client.get("/life/").content.decode()
        flew = next(p for p in found if p["a"].statement == "Flew to Example Junction")
        moved = next(p for p in found if p["a"].statement == "Helped Sam move")
        client.post("/life/pairs", {"a": flew["a"].claim_id, "b": flew["b"].claim_id, "choice": "a"})
        client.post("/life/pairs", {"a": moved["a"].claim_id, "b": moved["b"].claim_id, "choice": "both"})
        assert "Nothing to settle" in client.get("/life/pairs").content.decode()
    states = {c.claim_id: c.state for c in life.claims(world.store)}
    assert states[flew["b"].claim_id] == "rejected" and states[flew["a"].claim_id] == "accepted"


def test_a_persons_page_lists_their_message_days_from_an_export(world, tmp_path, monkeypatch):
    from towpath import connections, people

    library = tmp_path / "library"
    (library / "messages").mkdir(parents=True)
    (library / "messages" / "Sam.txt").write_text("Oct 01, 2026  9:00:00 AM\nSam\nLunch Friday?\n")
    monkeypatch.setenv("TOWPATH_LIBRARY", str(library))
    sid = connections.add_messages(world.store, library / "messages")
    connections.start_indexing(world.store, world.credentials, sid)
    connections.index_pending(world.config, world.credentials)
    chats = people.person(world.store, "sam@example.org")["chats"]  # Sam writes mail as "Sam"
    assert [c["subject"] for c in chats] == ["Messages with Sam"]
