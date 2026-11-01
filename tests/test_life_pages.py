"""The life story's pages: the timeline, people, the owner's memories, the story and the family edition."""

from datetime import datetime, timezone

import pytest
from django.test import Client, override_settings

from tests.life_world import OWNER, make_world
from towpath import life, life_ask, life_groups
from towpath.web import auth
from towpath.web.application import configure


@pytest.fixture
def world(tmp_path, openai_stub):
    yield from make_world(tmp_path, openai_stub)


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


def test_the_family_edition_holds_only_what_the_owner_shared_and_nothing_that_points_to_records(world, monkeypatch):
    from towpath import life_story

    life_groups.set_profile(world.store, "Reader", "1980-06-01")
    life_groups.propose_groups(world.worker())
    trip = next(g for g in life_groups.groups(world.store) if g["kind"] == "trip")
    life_groups.decide(world.store, trip, "kept")
    by_name = {c.statement: c for c in life.claims(world.store)}
    life.review(world.store, by_name["Started a new job"].claim_id, "accept")
    life.review(world.store, by_name["Flew to Example Junction"].claim_id, "accept")  # the others stay proposed
    swim = life.recollect(world.store, "Learned to swim", "1987", "year", "1987-01-01", "1987-12-31")
    assert life_story.story(world.store, family=True)["chapters"] == []  # nothing shared until the owner says so
    monkeypatch.setattr(auth, "_setup_codes", {})
    configure(world.store)
    auth.create_account(world.store, "owner", OWNER)
    with override_settings(TOWPATH_STORE_DIR=world.store, TOWPATH_CONFIG=world.config, ALLOWED_HOSTS=["testserver"]):
        client = Client()
        client.post("/login", {"username": "owner", "password": OWNER})
        assert 'name="kind" value="group"' in client.get("/life/story").content.decode()
        client.post("/life/share", {"kind": "group", "key": trip["group_id"], "on": "1"})
        client.post("/life/share", {"kind": "claim", "key": swim.claim_id, "on": "1"})
        assert client.post("/life/share", {"kind": "mail", "key": "x", "on": "1"}).status_code == 400
        page = client.get("/life/story?edition=family").content.decode()
        md = client.get("/life/story?edition=family&format=md")
        bad = client.post("/life/share", {"kind": "claim", "key": "clm_nothing", "on": "1"}).status_code
    assert bad == 400
    assert "Flew to Example Junction" in page and "Learned to swim" in page and "Started a new job" not in page
    assert "Stayed at the canal hotel" not in page  # in the shared memory, but never accepted
    assert "Trip to Example Junction" not in page and "Four days by the canal" not in page  # shown by its kind
    assert ">Sources</h2>" not in page and f"{world.sid}:" not in page and "1980-06" not in page  # no refs or birthday
    text = md.content.decode()
    assert md["Content-Disposition"].endswith('"family-edition.md"') and "[^" not in text and "The 1980s" in text
    life_story.share(world.store, "claim", swim.claim_id, False)
    assert "Learned to swim" not in life_story.markdown(life_story.story(world.store, family=True))
