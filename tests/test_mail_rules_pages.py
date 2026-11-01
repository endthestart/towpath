"""Mail rules and categories: the pages, learning from the owner, drafting rules, and rules from Inbox Zero."""

import json
from contextlib import closing

from django.test import Client, override_settings

import pytest

from tests.rules_world import OWNER, answer, make_world, run_all
from tests.test_mail_rules import _plan
from towpath.mail import executor, rules, senders, settings
from towpath.stores import open_store
from towpath.web import auth
from towpath.web.application import configure


@pytest.fixture
def world(tmp_path, openai_stub):
    yield from make_world(tmp_path, openai_stub)


def test_the_pages_for_senders_rules_and_batch_answers(world, monkeypatch):
    senders.categorize_pending(world.worker())
    monkeypatch.setattr(auth, "_setup_codes", {})
    configure(world.store)
    auth.create_account(world.store, "owner", OWNER)
    with override_settings(TOWPATH_STORE_DIR=world.store, TOWPATH_CONFIG=world.config, ALLOWED_HOSTS=["testserver"]):
        client = Client()
        client.post("/login", {"username": "owner", "password": OWNER})
        found = senders.inbox(world.store, world.sid)
        assert found["total"] == 3 and {c["category"] for c in found["categories"]} >= {"marketing"}
        assert f"/mail/{world.sid}/?box=INBOX&amp;category=marketing" in client.get("/mail/").content.decode()
        listed = client.get(f"/mail/{world.sid}/", {"box": "INBOX", "category": "marketing"}).content.decode()
        assert "Big sale" in listed and "Saturday?" not in listed and "Clean these up" in listed
        from test_mail_threads import mail

        from towpath import connect
        from towpath.mail import analysis

        world.state.mailboxes["INBOX"].messages[30] = mail(30, "Example ID <security@id.example>", "reader@example.com",
                                                           "Your sign-in code", "Use 123456 to sign in.", 2)
        connect.sync(world.worker(), world.sid)
        analysis.analyze_pending(world.worker())
        found = senders.accounts(world.store, world.sid)
        assert [(a["domain"], a["messages"]) for a in found] == [("id.example", 1)]
        assert "id.example" in client.get(f"/mail/{world.sid}/accounts").content.decode()
        rules_page = client.get(f"/mail/{world.sid}/rules").content.decode()
        assert "· would look at 1 message in your inbox now" in rules_page.split("Archive marketing")[1][:80]
        page = client.get(f"/mail/{world.sid}/senders").content.decode()
        assert "news@shop.example" in page and "Sale announcements." in page
        client.post("/mail/sender-category", {"source_id": world.sid, "address": "news@shop.example",
                                              "category": "newsletter"})
        assert senders.categories(world.store, world.sid)["news@shop.example"] == "newsletter"
        client.post("/mail/sender-category-many", {"source_id": world.sid, "category": "person",
                                                   "address": ["news@shop.example", "dana@example.net"]})
        cats = senders.categories(world.store, world.sid)
        assert cats["news@shop.example"] == "person" and cats["dana@example.net"] == "person"
        assert client.post("/mail/sender-category-many", {"source_id": world.sid, "category": "person"}
                           ).status_code == 400
        client.post("/mail/sender-category", {"source_id": world.sid, "address": "news@shop.example",
                                              "category": "newsletter"})
        form = {"name": "Shop", "from": "*@shop.example", "mailbox": "INBOX", "action_0": "add_keyword",
                "param_0": "Shop", "scope": "all"}
        preview = client.post(f"/mail/{world.sid}/rules/new", {**form, "preview": "1"}).content.decode()
        assert "Big sale" in preview and "Saturday?" not in preview
        assert client.post(f"/mail/{world.sid}/rules/new", {**form, "action_0": ""}).status_code == 400
        assert client.post(f"/mail/{world.sid}/rules/new", form).status_code == 302
        rules_page = client.get(f"/mail/{world.sid}/rules").content.decode()
        assert "Shop" in rules_page and "asks you first" in rules_page
        run_all(world)
        mail_page = client.get("/mail/").content.decode()
        assert "Approve all 1" in mail_page
        producer = _plan(world)[0]["producer"]
        client.post("/mail/answer-batch", {"producer": producer, "approve": "1"})
        assert "Approve all" not in client.get("/mail/").content.decode()
        executor.run_pending(world.worker(), world.credentials, pause=0)
        assert executor.receipts(world.store)[0]["outcome"] == "dry-run"
        rule_id = rules.all_rules(world.store, world.sid, "web")[0]["rule_id"]
        history = client.get(f"/mail/{world.sid}/rules/{rule_id}/").content.decode()
        assert "approved 1" in history and "Big sale" in history
    assert settings.dry_run(world.store, world.sid)


def test_a_category_the_owner_gives_one_sender_is_learned_for_its_domain(world, monkeypatch):
    from test_mail_threads import mail

    from towpath import connect
    from towpath.mail import analysis

    world.state.mailboxes["INBOX"].messages[9] = mail(9, "Shop Orders <orders@shop.example>", "reader@example.com",
                                                      "Your order", "Thanks for your order.", 1)
    monkeypatch.setattr(senders.rebuilding, "SHARED_DOMAINS", {"mail.example"})  # a personal mail provider, as gmail is
    world.state.mailboxes["INBOX"].messages[10] = mail(10, "Friend <friend@mail.example>", "reader@example.com",
                                                       "hi", "hello", 1)
    connect.sync(world.worker(), world.sid)
    analysis.analyze_pending(world.worker())
    senders.mark(world.store, world.sid, "news@shop.example", "receipt")
    senders.mark(world.store, world.sid, "someone@mail.example", "receipt")  # a shared domain: nothing learned
    senders.categorize_pending(world.worker())
    got = {r["address"]: r for r in senders.listing(world.store, world.sid)}
    learned = got["orders@shop.example"]
    assert learned["category"] == "receipt" and learned["category_by"] == "learned"
    assert got["friend@mail.example"]["category_by"] != "learned"


def test_the_owners_marks_are_examples_for_the_model(world):
    from towpath.mail import threads

    threads.track_pending(world.worker())
    key = next(r["thread_key"] for r in threads.listing(world.store, world.sid, "needs_owner_reply"))
    threads.mark(world.store, world.sid, key, "done")
    with closing(open_store(world.store, "mailintel", "worker")) as intel:
        text = threads._owner_examples(intel, world.store, world.sid)
    assert '"Saturday?": done' in text and "follow their judgement" in text


def test_a_rule_described_in_words_is_drafted_by_the_model_for_the_owner_to_check(world, monkeypatch):
    from towpath.mail import rule_drafts

    def drafting(prompt):
        if "Turn the owner's description into one mail rule" in prompt:
            return json.dumps({"name": "Shop sales", "from": "*@shop.example", "subject": "", "categories": [],
                               "instructions": "", "actions": [{"type": "add_keyword", "value": "Shop"},
                                                               {"type": "archive", "value": ""}], "scope": "new"})
        return answer(prompt)

    world.model_stub.respond = drafting
    monkeypatch.setattr(auth, "_setup_codes", {})
    configure(world.store)
    auth.create_account(world.store, "owner", OWNER)
    with override_settings(TOWPATH_STORE_DIR=world.store, TOWPATH_CONFIG=world.config, ALLOWED_HOSTS=["testserver"]):
        client = Client()
        client.post("/login", {"username": "owner", "password": OWNER})
        asked = client.post(f"/mail/{world.sid}/rules/describe", {"description": "label shop mail and archive it"})
        assert asked.status_code == 302
        assert "is drafting this rule" in client.get(asked["Location"]).content.decode()
        assert rule_drafts.draft_pending(world.worker()) == [{"step": "rule-drafts", "drafted": 1}]
        page = client.get(asked["Location"]).content.decode()
        assert "Drafted from" in page and "*@shop.example" in page and 'value="Shop"' in page
        assert rules.all_rules(world.store, world.sid, "web") == []  # nothing is saved until the owner saves it
        assert rule_drafts.draft_pending(world.worker()) == []
    world.model_stub.respond = lambda prompt: json.dumps({"name": "x", "from": "", "subject": "", "categories": [],
                                                          "instructions": "", "actions": [{"type": "move",
                                                                                           "value": ""}],
                                                          "scope": "new"})
    seq = rule_drafts.ask(world.store, world.sid, "move it somewhere")
    rule_drafts.draft_pending(world.worker())
    found = rule_drafts.draft(world.store, seq)
    assert found["status"] == "words-after-invalid" and found["body"]["instructions"] == "move it somewhere"


def test_mail_from_companies_is_fyi_without_the_model_until_the_owner_says_its_a_person(world):
    from test_mail_threads import mail

    from towpath import connect
    from towpath.mail import analysis, threads

    world.state.mailboxes["INBOX"].messages[11] = mail(11, "Shop Deals <deals@shop.example>", "reader@example.com",
                                                       "For you", "Want 20% off this weekend?", 1)
    connect.sync(world.worker(), world.sid)
    analysis.analyze_pending(world.worker())
    senders.categorize_pending(world.worker())
    threads.track_pending(world.worker())

    def deal():
        return next(t for s in threads.STATES for t in threads.listing(world.store, world.sid, s)
                    if t["subject"] == "For you")

    assert deal()["state"] == "fyi" and deal()["state_by"] == "rule-sender"
    assert not any("20% off" in p for p in world.model_prompts() if "<conversation>" in p)
    senders.mark(world.store, world.sid, "deals@shop.example", "person")
    threads.track_pending(world.worker())
    assert deal()["state_by"] == "model"  # back to the model once the owner calls the sender a person


def test_a_sender_called_a_person_gets_the_rules_state_back_without_a_model(world):
    from test_mail_threads import mail

    from towpath import connect, decisions
    from towpath.mail import analysis, threads

    world.state.mailboxes["INBOX"].messages[11] = mail(11, "Shop Deals <deals@shop.example>", "reader@example.com",
                                                       "For you", "Want 20% off this weekend?", 1)
    connect.sync(world.worker(), world.sid)
    analysis.analyze_pending(world.worker())
    senders.categorize_pending(world.worker())
    decisions.revoke_model(world.worker(), world.model, "content")  # rules only from here on
    threads.track_pending(world.worker())
    senders.mark(world.store, world.sid, "deals@shop.example", "person")
    threads.track_pending(world.worker())
    deal = next(t for s in threads.STATES for t in threads.listing(world.store, world.sid, s)
                if t["subject"] == "For you")
    assert deal["state"] == "needs_owner_reply" and deal["state_by"] == "rule"


def test_the_rules_rewritten_from_inbox_zero_are_added_switched_off_once(world, monkeypatch):
    from django.test import Client, override_settings

    from towpath.web import auth
    from towpath.web.application import configure

    monkeypatch.setattr(auth, "_setup_codes", {})
    configure(world.store)
    auth.create_account(world.store, "owner", "correct horse battery staple")
    with override_settings(TOWPATH_STORE_DIR=world.store, TOWPATH_CONFIG=world.config, ALLOWED_HOSTS=["testserver"]):
        client = Client()
        client.post("/login", {"username": "owner", "password": "correct horse battery staple"})
        assert "Rules from Inbox Zero" in client.get(f"/mail/{world.sid}/rules").content.decode()
        client.post(f"/mail/{world.sid}/rules/inbox-zero")
        client.post(f"/mail/{world.sid}/rules/inbox-zero")  # twice: nothing added the second time
    added = rules.all_rules(world.store, world.sid)
    assert len(added) == 6 and all(r["enabled"] == 0 and r["mode"] == "propose" for r in added)
    marketing = next(r for r in added if r["body"]["name"].startswith("Marketing"))
    assert [a["type"] for a in marketing["body"]["actions"]] == ["add_keyword", "archive"]
