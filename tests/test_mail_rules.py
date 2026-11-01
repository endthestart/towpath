"""Senders, categories and the owner's rules: rules and the model categorize senders, the owner's category wins,
rules propose changes that wait for approval or (switched to automatic, reversible only) run by themselves, still as
dry runs while the account is in dry runs only."""

import json

import pytest

from tests.rules_world import make_world, run_all
from towpath.mail import executor, rules, senders


@pytest.fixture
def world(tmp_path, openai_stub):
    yield from make_world(tmp_path, openai_stub)


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
