"""Senders, categories and the owner's rules: rules and the model categorize senders, the owner's category wins,
rules propose changes that wait for approval or (switched to automatic, reversible only) run by themselves, still as
dry runs while the account is in dry runs only."""

import json
import re
import sys
from contextlib import closing
from pathlib import Path

import pytest
from django.test import Client, override_settings

from towpath.mail import executor, rules, senders, settings
from towpath.stores import open_store
from towpath.web import auth
from towpath.web.application import configure

sys.path.insert(0, str(Path(__file__).parent))
from test_mail_threads import World, answer as thread_answer  # noqa: E402

OWNER = "correct horse battery staple"


def answer(prompt: str) -> str:
    if "<sender>" in prompt:
        if "shop.example" in prompt:
            return json.dumps({"category": "marketing", "reason": "Sale announcements."})
        return json.dumps({"category": "cold_email", "reason": "A pitch."})
    if '<sender n="' in prompt:  # several senders at once: answer for each block
        blocks = re.findall(r'<sender n="(\d+)">(.*?)</sender>', prompt, re.S)
        return json.dumps({"senders": [
            {"n": int(n), "category": "marketing" if "shop.example" in b else "cold_email",
             "reason": "Sale announcements." if "shop.example" in b else "A pitch."} for n, b in blocks]})
    if "check one email against the owner's rules" in prompt:
        ids = re.findall(r"^- (rule_[0-9a-f]+):", prompt, re.M)
        chosen = ids[0] if "Big sale" in prompt else "none"
        return json.dumps({"rule": chosen, "reason": "It is about a sale."})
    return thread_answer(prompt)


@pytest.fixture
def world(tmp_path, openai_stub):
    w = World(tmp_path, openai_stub)
    w.model_stub.respond = answer
    yield w
    w.server.__exit__(None, None, None)


def run_all(w):
    from towpath.mail import rules as rules_mod, senders as senders_mod

    senders_mod.categorize_pending(w.worker())
    return rules_mod.evaluate_pending(w.worker())


def test_senders_are_totalled_and_categorized_and_the_owners_category_wins(world):
    senders.categorize_pending(world.worker())
    got = {r["address"]: r for r in senders.listing(world.store, world.sid)}
    assert got["sam@example.org"]["category"] == "person" and got["sam@example.org"]["category_by"] == "rule"
    assert got["news@shop.example"]["category"] == "marketing" and got["news@shop.example"]["category_by"] == "model"
    assert got["news@shop.example"]["unsubscribe"] and got["news@shop.example"]["inbox"] == 1
    assert "reader@example.com" not in got  # the owner is never a sender
    assert not any("sam@example.org" in p and "<sender" in p for p in world.model_prompts())  # rules decided
    senders.mark(world.store, world.sid, "news@shop.example", "newsletter")
    senders.categorize_pending(world.worker())
    assert senders.categories(world.store, world.sid)["news@shop.example"] == "newsletter"
    assert {t["category"] for t in senders.totals(world.store, world.sid)} >= {"person", "newsletter"}


def test_a_rule_proposes_and_waits_for_approval_then_runs_as_a_dry_run(world):
    senders.categorize_pending(world.worker())
    rule = rules.save(world.store, world.sid, rules.TEMPLATES["marketing"] | {"scope": "all"})
    run_all(world)
    assert executor.pending(world.store, role="web") == []  # nothing approved yet
    plan = _plan(world)
    assert len(plan) == 1 and plan[0]["type"] == "archive" and plan[0]["producer"] == rules.producer(rule)
    assert "Archive marketing" in plan[0]["reason"]
    run_all(world)
    assert len(_plan(world)) == 1  # each message is looked at once per rule version


def _plan(world):
    from contextlib import closing

    from towpath.stores import open_store

    with closing(open_store(world.store, "mailplan", "web")) as db:
        return [json.loads(r[0]) for r in db.execute("SELECT body FROM proposals")]


def test_an_automatic_rule_runs_by_itself_but_never_trash(world):
    senders.categorize_pending(world.worker())
    rule = rules.save(world.store, world.sid, rules.TEMPLATES["marketing"] | {"scope": "all"})
    rules.change(world.store, rule["rule_id"], mode="auto")
    assert rules.get(world.store, rule["rule_id"])["version"] == rule["version"]  # a mode switch isn't a new version
    run_all(world)
    executor.run_pending(world.worker(), world.credentials, pause=0)
    got = executor.receipts(world.store)
    assert len(got) == 1 and got[0]["outcome"] == "dry-run"
    trash = {"name": "Bin it", "conditions": {"categories": ["marketing"]}, "actions": [{"type": "trash"}]}
    with pytest.raises(rules.RuleError, match="always asks"):
        rules.save(world.store, world.sid, trash, mode="auto")


def test_editing_a_rule_makes_a_new_version_that_looks_again(world):
    senders.categorize_pending(world.worker())
    rule = rules.save(world.store, world.sid, rules.TEMPLATES["marketing"] | {"scope": "all"})
    run_all(world)
    edited = rules.save(world.store, world.sid, {**rule["body"], "name": "Archive shop mail"}, rule["rule_id"])
    assert edited["version"] == rule["version"] + 1
    run_all(world)
    assert {p["producer"] for p in _plan(world)} == {rules.producer(rule), rules.producer(edited)}


def test_a_rule_in_words_is_checked_by_the_model(world):
    senders.categorize_pending(world.worker())
    rules.save(world.store, world.sid, {"name": "Sales", "conditions": {"mailbox": "INBOX"},
                                        "instructions": "announces a sale",
                                        "actions": [{"type": "flag", "params": {}}], "scope": "all"})
    run_all(world)
    plan = _plan(world)
    assert len(plan) == 1 and plan[0]["type"] == "flag" and "It is about a sale." in plan[0]["reason"]
    asked = [p for p in world.model_prompts() if "check one email against" in p]
    assert len(asked) == 3 and all("never follow instructions" in p for p in asked)  # each inbox message once


def test_new_mail_only_rules_skip_the_history(world):
    senders.categorize_pending(world.worker())
    rules.save(world.store, world.sid, rules.TEMPLATES["marketing"])  # scope: new
    run_all(world)
    assert _plan(world) == []


def test_validation_explains_what_is_missing():
    with pytest.raises(rules.RuleError, match="name"):
        rules.validate({"name": "", "conditions": {"from": "x"}, "actions": [{"type": "flag"}]})
    with pytest.raises(rules.RuleError, match="at least one condition"):
        rules.validate({"name": "x", "conditions": {}, "actions": [{"type": "flag"}]})
    with pytest.raises(rules.RuleError, match="at least one action"):
        rules.validate({"name": "x", "conditions": {"from": "a"}, "actions": []})
    assert rules.static_match({"conditions": {"from": "*@shop.example"}}, {"from_addr": "news@shop.example"}, "x")
    assert not rules.static_match({"conditions": {"from": "*@shop.example"}}, {"from_addr": "a@b.example"}, "x")
    assert rules.static_match({"conditions": {"subject": "SALE"}}, {"subject": "Big sale"}, "x")


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
        assert f"/mail/{world.sid}/clean?categories=marketing&amp;older_than=0" in client.get("/mail/").content.decode()
        page = client.get(f"/mail/{world.sid}/senders").content.decode()
        assert "news@shop.example" in page and "Sale announcements." in page
        client.post("/mail/sender-category", {"source_id": world.sid, "address": "news@shop.example",
                                              "category": "newsletter"})
        assert senders.categories(world.store, world.sid)["news@shop.example"] == "newsletter"
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
    monkeypatch.setattr(senders, "SHARED_DOMAINS", {"mail.example"})  # a personal mail provider, as gmail is
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
