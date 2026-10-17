"""Purchases read from receipts by the model: candidates by sender and subject, each message once, listed per order."""

import json
import sys
from pathlib import Path

import pytest
from django.test import Client, override_settings

from towpath import connect
from towpath.mail import analysis, purchases
from towpath.web import auth
from towpath.web.application import configure

sys.path.insert(0, str(Path(__file__).parent))
from test_mail_threads import World, answer as thread_answer, mail  # noqa: E402

OWNER = "correct horse battery staple"


def answer(prompt: str) -> str:
    if "Is it a receipt, order confirmation or invoice" in prompt:
        if "Order #A100" in prompt:
            return json.dumps({"purchase": True, "merchant": "Acme Outdoor", "day": "2026-10-10", "total": 129.5,
                               "currency": "usd", "items": ["Two-person tent"], "order": "A100"})
        return json.dumps({"purchase": False, "merchant": None, "day": None, "total": None, "currency": None,
                           "items": [], "order": None})
    return thread_answer(prompt)


@pytest.fixture
def world(tmp_path, openai_stub):
    w = World(tmp_path, openai_stub)
    w.model_stub.respond = answer
    inbox = w.state.mailboxes["INBOX"].messages
    inbox[20] = mail(20, "Acme <orders@acme.example>", "reader@example.com", "Your order confirmation",
                     "Order #A100. Two-person tent. Total: $129.50", 7)
    inbox[21] = mail(21, "Acme <orders@acme.example>", "reader@example.com", "Your order has shipped",
                     "Order #A100 is on its way. Total: $129.50", 5)  # a shipping notice: not a candidate
    inbox[22] = mail(22, "Acme <orders@acme.example>", "reader@example.com", "Order now and save",
                     "Big savings this week, $10 off.", 3)  # the model says it isn't a purchase
    inbox[23] = mail(23, "Club <club@club.example>", "reader@example.com", "Your receipt",
                     "Thanks for coming.", 2)  # no amount: never sent to the model
    connect.sync(w.worker(), w.sid)
    analysis.analyze_pending(w.worker())
    yield w
    w.server.__exit__(None, None, None)


def test_receipts_become_purchases_and_other_mail_is_looked_at_once(world):
    assert purchases.read_pending(world.worker())[0] == {"source_id": world.sid, "step": "mail-purchases", "read": 2,
                                                         "purchases": 1}
    asked = [p for p in world.model_prompts() if "Is it a receipt" in p]
    assert len(asked) == 2 and not any("on its way" in p or "Thanks for coming" in p for p in asked)
    assert purchases.read_pending(world.worker()) == []  # each message once
    rows = purchases.listing(world.store, world.sid)
    assert [(r["merchant"], r["total"], r["currency"], r["items"]) for r in rows] == \
        [("Acme Outdoor", 129.5, "USD", ["Two-person tent"])]
    assert purchases.listing(world.store, world.sid, "tent") and not purchases.listing(world.store, world.sid, "sofa")
    found = purchases.summary(world.store, world.sid)
    assert found["count"] == 1 and found["years"] == [("2026", "USD", 1, 129.5)] and found["looked"] == 3


def test_the_purchases_page_and_ask(world, monkeypatch):
    from towpath import life_ask
    from towpath.unified import grants

    purchases.read_pending(world.worker())
    grants.grant(world.store, world.sid, "life-evidence", author="owner")
    assert any(s["kind"] == "purchase" and "Two-person tent" in s["text"]
               for s in life_ask.evidence(world.worker(), "When did I buy the tent?"))
    monkeypatch.setattr(auth, "_setup_codes", {})
    configure(world.store)
    auth.create_account(world.store, "owner", OWNER)
    with override_settings(TOWPATH_STORE_DIR=world.store, TOWPATH_CONFIG=world.config, ALLOWED_HOSTS=["testserver"]):
        client = Client()
        client.post("/login", {"username": "owner", "password": OWNER})
        page = client.get(f"/mail/{world.sid}/purchases").content.decode()
        assert "Acme Outdoor" in page and "Two-person tent" in page and "129.50" in page
        assert "No purchases match" in client.get(f"/mail/{world.sid}/purchases?q=sofa").content.decode()
