"""What the life story keeps and hands over: the export, pairs to settle, people, and copies of evidence."""

import json
from contextlib import closing

import pytest
from django.test import Client, override_settings

from tests.life_world import OWNER, claim, make_world
from towpath import life
from towpath.mail import senders
from towpath.stores import open_store
from towpath.unified import grants
from towpath.web import auth
from towpath.web.application import configure


@pytest.fixture
def world(tmp_path, openai_stub):
    yield from make_world(tmp_path, openai_stub)


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
    (library / "messages" / "Sam Rivera.txt").write_text("Oct 01, 2026  9:00:00 AM\nSam Rivera\nLunch Friday?\n")
    (library / "messages" / "Sam.txt").write_text("Oct 01, 2026  9:00:00 AM\nSam\nSomeone else called Sam\n")
    monkeypatch.setenv("TOWPATH_LIBRARY", str(library))
    sid = connections.add_messages(world.store, library / "messages")
    connections.start_indexing(world.store, world.credentials, sid)
    connections.index_pending(world.config, world.credentials)
    assert people.person(world.store, "sam@example.org")["chats"] == []  # "Sam" alone would match strangers
    from test_mail_threads import ME, mail

    from towpath import connect
    from towpath.mail import analysis

    for n in (50, 51):
        world.state.mailboxes["INBOX"].messages[n] = mail(n, "Sam Rivera <sam@example.org>", ME, "Hi", "Hi", 1)
    connect.sync(world.worker(), world.sid)
    analysis.analyze_pending(world.worker())
    chats = people.person(world.store, "sam@example.org")["chats"]  # now writes mail as "Sam Rivera"
    assert [c["subject"] for c in chats] == ["Messages with Sam Rivera"]


def test_keeping_the_evidence_copies_kept_events_messages_and_follows_the_owners_decisions(world, monkeypatch):
    from towpath import life_evidence, life_story

    helped = next(c for c in life.claims(world.store) if c.statement == "Helped Sam move")
    life.review(world.store, helped.claim_id, "accept")
    assert life_evidence.keep_pending(world.worker()) == []  # off until the owner turns it on
    life_evidence.set_enabled(world.store, True)
    assert life_evidence.keep_pending(world.worker())[0]["kept"] == 1
    text = life_evidence.copy(world.store, helped.claim_id).decode()
    assert "Event: Helped Sam move" in text and "Subject: Saturday?" in text
    assert life_evidence.keep_pending(world.worker()) == []  # nothing new
    monkeypatch.setattr(auth, "_setup_codes", {})
    configure(world.store)
    auth.create_account(world.store, "owner", OWNER)
    with override_settings(TOWPATH_STORE_DIR=world.store, TOWPATH_CONFIG=world.config, ALLOWED_HOSTS=["testserver"]):
        client = Client()
        client.post("/login", {"username": "owner", "password": OWNER})
        assert f"/life/evidence/{helped.claim_id}" in client.get("/life/story").content.decode()
        got = client.get(f"/life/evidence/{helped.claim_id}")
        assert got.status_code == 200 and got["Content-Disposition"].startswith("attachment")
        assert client.get("/life/evidence/..%2F..%2Fdecisions.db").status_code == 404
        assert "1 copy kept" in client.get("/life/").content.decode()
    life.review(world.store, helped.claim_id, "reject")  # no longer kept: its copy goes
    assert life_evidence.keep_pending(world.worker())[0]["dropped"] == 1
    assert life_evidence.copy(world.store, helped.claim_id) is None
    life.review(world.store, helped.claim_id, "accept")
    life_evidence.keep_pending(world.worker())
    life_evidence.set_enabled(world.store, False)
    assert life_evidence.copy(world.store, helped.claim_id) is None  # not served once keeping is off
    life_evidence.set_enabled(world.store, True)
    life.review(world.store, helped.claim_id, "reject")
    assert life_evidence.copy(world.store, helped.claim_id) is None  # nor once the event isn't kept
    life.review(world.store, helped.claim_id, "accept")
    (life_evidence.folder(world.store) / "stray.txt").write_text("left by a crash")
    life_evidence.set_enabled(world.store, False)  # off deletes every copy
    assert life_evidence.keep_pending(world.worker())[0]["step"] == "life-evidence-deleted"
    assert not list(life_evidence.folder(world.store).glob("*.txt"))
    assert life_story.story(world.store)["notes"]


def test_the_owner_joins_a_persons_addresses_and_towpath_only_suggests(world, monkeypatch):
    from test_mail_threads import ME, mail

    from towpath import connect, people
    from towpath.mail import analysis

    inbox = world.state.mailboxes["INBOX"].messages
    inbox[40] = mail(40, "Sam Rivera <sam@example.org>", ME, "Lunch", "Lunch Friday?", 1)
    inbox[41] = mail(41, "Sam Rivera <sam@example.org>", ME, "Photos", "Here are the photos.", 1)
    inbox[42] = mail(42, "Sam Rivera <sam.rivera@work.example>", ME, "From work", "My work address.", 1)
    connect.sync(world.worker(), world.sid)
    analysis.analyze_pending(world.worker())
    for address in ("sam@example.org", "sam.rivera@work.example"):
        senders.mark(world.store, world.sid, address, "person")
    senders.categorize_pending(world.worker())
    assert [s["address"] for s in people.suggestions(world.store, "sam@example.org")] == ["sam.rivera@work.example"]
    before = {p["address"] for p in people.everyone(world.store)}
    assert {"sam@example.org", "sam.rivera@work.example"} <= before  # nothing joined by Towpath itself
    monkeypatch.setattr(auth, "_setup_codes", {})
    configure(world.store)
    auth.create_account(world.store, "owner", OWNER)
    with override_settings(TOWPATH_STORE_DIR=world.store, TOWPATH_CONFIG=world.config, ALLOWED_HOSTS=["testserver"]):
        client = Client()
        client.post("/login", {"username": "owner", "password": OWNER})
        assert "the same person?" in client.get("/people/sam@example.org/").content.decode()
        client.post("/people/sam@example.org/join", {"other": "sam.rivera@work.example"})
        assert client.get("/people/sam.rivera@work.example/")["Location"] == "/people/sam@example.org/"
        page = client.get("/people/sam@example.org/").content.decode()
        assert "joined by you" in page and "From work" in page and "Lunch" in page
        assert client.post("/people/sam@example.org/join", {"other": "not an address"}).status_code == 400
    rows = [p for p in people.everyone(world.store) if "sam" in p["address"]]
    assert [p["address"] for p in rows] == ["sam@example.org"] and rows[0]["also"] == ["sam.rivera@work.example"]
    people.separate(world.store, "sam.rivera@work.example")
    assert people.addresses(world.store, "sam@example.org") == ["sam@example.org"]
    with pytest.raises(ValueError):
        people.join(world.store, "sam@example.org", "stranger@nowhere.example")  # never seen: refused
    with pytest.raises(ValueError):
        people.separate(world.store, "sam@example.org")  # not joined to anyone
    # A loop only a damaged store could hold still resolves, so pages never redirect forever.
    with closing(open_store(world.store, "decisions", "web")) as db:
        for a, b in (("sam@example.org", "sam.rivera@work.example"), ("sam.rivera@work.example", "sam@example.org")):
            db.execute("INSERT OR REPLACE INTO settings VALUES (?, 'person', ?, 'x', 't')", (f"address:{a}", f'"{b}"'))
        db.commit()
    assert people.primary(world.store, "sam@example.org") == "sam@example.org"


def test_a_copy_that_cannot_be_written_never_stops_the_worker_and_is_tried_later(world, monkeypatch):
    from towpath import life_evidence

    helped = next(c for c in life.claims(world.store) if c.statement == "Helped Sam move")
    life.review(world.store, helped.claim_id, "accept")
    life_evidence.set_enabled(world.store, True)

    def full(*_):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(life_evidence, "_write", full)
    assert life_evidence.keep_pending(world.worker()) == []  # recorded, not raised
    with closing(open_store(world.store, "life", "web")) as db:
        status, next_try = db.execute("SELECT status, next_try FROM kept_evidence").fetchone()
    assert status == "failed: OSError" and next_try
    monkeypatch.undo()
    assert life_evidence.keep_pending(world.worker()) == []  # not before its time
    with closing(open_store(world.store, "life", "worker")) as db:
        db.execute("UPDATE kept_evidence SET next_try = '2000-01-01'")
        db.commit()
    assert life_evidence.keep_pending(world.worker())[0]["kept"] == 1
