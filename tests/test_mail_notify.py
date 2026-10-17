"""Alerts, the digest and statistics: proposed by the worker, approved by the owner's own setting, dry runs until
the owner turns the kind of change on."""

import sys
from datetime import datetime, timezone
from email import message_from_bytes, policy
from pathlib import Path

import pytest
from django.test import Client, override_settings

from towpath.mail import executor, notify, settings, threads
from towpath.web import auth
from towpath.web.application import configure

sys.path.insert(0, str(Path(__file__).parent))
from test_mail_threads import WRITE, World  # noqa: E402

OWNER = "correct horse battery staple"


@pytest.fixture
def world(tmp_path, openai_stub):
    w = World(tmp_path, openai_stub)
    threads.track_pending(w.worker())
    yield w
    w.server.__exit__(None, None, None)


def live(w, *types):
    executor.password_path(w.credentials, w.sid).write_text(WRITE)
    settings.set_enabled_types(w.store, w.sid, set(types))
    settings.set_dry_run(w.store, w.sid, False)


def test_alerts_flag_conversations_that_newly_need_a_reply(world):
    notify.set_alerts(world.store, world.sid, "flag")
    assert notify.alerts_pending(world.worker()) == []  # only mail arriving after alerts were turned on
    settings._set(world.store, world.sid, "alerts", {"mode": "flag", "since": "2020-01-01T00:00:00+00:00"},
                  "mail-alerts", "test")
    assert notify.alerts_pending(world.worker())[0]["alerts"] == 1
    assert notify.alerts_pending(world.worker()) == []  # once per message
    executor.run_pending(world.worker(), world.credentials, pause=0)
    assert executor.receipts(world.store)[0]["outcome"] == "dry-run"
    live(world, "flag")
    notify.set_alerts(world.store, world.sid, "off")  # turned off: waiting alerts are no longer approved
    assert executor.pending(world.store) == []


def test_the_digest_goes_into_the_inbox_once_per_period(world):
    notify.set_digest(world.store, world.sid, "daily")
    morning = datetime(2026, 10, 17, 13, 0, tzinfo=timezone.utc)  # 8am in Chicago
    assert notify.digests_pending(world.worker(), at=datetime(2026, 10, 17, 10, 0, tzinfo=timezone.utc)) == []
    assert notify.digests_pending(world.worker(), at=morning)[0]["period"] == "2026-10-17"
    assert notify.digests_pending(world.worker(), at=morning) == []
    live(world, "inbox_note")
    executor.run_pending(world.worker(), world.credentials, pause=0)
    notes = [m for m in world.state.mailboxes["INBOX"].messages.values() if b"Towpath digest" in m.raw]
    assert len(notes) == 1
    text = message_from_bytes(notes[0].raw, policy=policy.default).get_content()
    assert "Waiting for your reply (1)" in text and "Saturday?" in text and "Deck quote" in text
    assert "no answer for 4 days" in text


def test_statistics_are_measured_and_shown(world, monkeypatch):
    found = notify.measure(world.store, world.sid)
    assert dict(found["top_correspondents"]) == {"dana@example.net": 1, "pat@example.org": 1, "sam@example.org": 1}
    assert sum(w[1] for w in found["weeks"]) == 3 and sum(w[2] for w in found["weeks"]) == 3
    monkeypatch.setattr(auth, "_setup_codes", {})
    configure(world.store)
    auth.create_account(world.store, "owner", OWNER)
    with override_settings(TOWPATH_STORE_DIR=world.store, TOWPATH_CONFIG=world.config, ALLOWED_HOSTS=["testserver"]):
        client = Client()
        client.post("/login", {"username": "owner", "password": OWNER})
        assert "dana@example.net" in client.get(f"/mail/{world.sid}/stats").content.decode()
        client.post("/mail/tell-me", {"source_id": world.sid, "alerts": "keyword", "digest": "weekly"})
        assert notify.alert_setting(world.store, world.sid)["mode"] == "keyword"
        assert notify.digest_setting(world.store, world.sid)["every"] == "weekly"
        assert client.post("/mail/tell-me", {"source_id": world.sid, "alerts": "loud"}).status_code == 400
        client.post("/mail/tell-me", {"source_id": world.sid, "follow_up": "7", "reply_labels": "1"})
        assert notify.follow_up_days(world.store, world.sid) == 7
        assert notify.reply_labels_setting(world.store, world.sid)["on"]
        client.post("/mail/draft-notes", {"source_id": world.sid, "notes": "Sign off with just M."})
        page = client.get("/mail/").content.decode()
        assert "Sign off with just M." in page and '<option value="7" selected>' in page
        assert client.post("/mail/draft-notes", {"source_id": world.sid, "notes": "x" * 2001}).status_code == 400


def test_a_follow_up_reminder_labels_the_unanswered_message_drafts_a_nudge_and_clears_on_an_answer(world):
    import json

    from test_mail_threads import answer, mail

    from towpath import connect
    from towpath.mail import analysis

    def drafting(prompt):
        if "draft a short follow-up" in prompt:
            return json.dumps({"body": "Just checking in on the deck quote."})
        return answer(prompt)

    world.model_stub.respond = drafting
    notify.set_follow_up(world.store, world.sid, 3)
    threads.set_notes(world.store, world.sid, "I'm free weekday mornings.")
    threads.track_pending(world.worker())
    row = {r["subject"]: r for r in threads.listing(world.store, world.sid, "awaiting_others")}["Deck quote"]
    assert row["draft"] == "Just checking in on the deck quote."
    assert any("I'm free weekday mornings." in p for p in world.model_prompts())
    nudge = message_from_bytes(threads.draft_message(world.store, world.sid, row["thread_key"], row["draft"]).encode(),
                               policy=policy.default)
    assert nudge["To"] == "dana@example.net"  # only the owner wrote: to whoever they wrote to
    assert notify.follow_ups_pending(world.worker())[0]["labelled"] == 1
    assert notify.follow_ups_pending(world.worker()) == []  # once per message
    live(world, "add_keyword", "remove_keyword")
    executor.run_pending(world.worker(), world.credentials, pause=0)
    sent = world.state.mailboxes["Sent"].messages[1]
    assert "Follow-up" in sent.flags
    world.state.mailboxes["INBOX"].messages[4] = mail(7, "Dana <dana@example.net>", "reader@example.com",
                                                      "Re: Deck quote", "Here it is.", 0, reply_to=2)
    connect.sync(world.worker(), world.sid)
    analysis.analyze_pending(world.worker())
    threads.track_pending(world.worker())
    assert notify.follow_ups_pending(world.worker())[0]["cleared"] == 1
    executor.run_pending(world.worker(), world.credentials, pause=0)
    assert "Follow-up" not in sent.flags
    with pytest.raises(ValueError):
        notify.set_follow_up(world.store, world.sid, 5)


def test_reply_labels_follow_the_conversation_and_come_off_when_it_moves_on(world):
    import json

    from test_mail_threads import answer, mail

    from towpath import connect
    from towpath.mail import analysis

    world.model_stub.respond = lambda p: json.dumps({"state": "needs_owner_reply", "reason": "A question."}) \
        if "Any questions?" in p else answer(p)
    assert notify.reply_labels_pending(world.worker()) == []  # off
    notify.set_reply_labels(world.store, world.sid, True)
    assert notify.reply_labels_pending(world.worker())[0]["added"] == 2  # Saturday? and Deck quote
    assert notify.reply_labels_pending(world.worker()) == []
    live(world, "add_keyword", "remove_keyword")
    executor.run_pending(world.worker(), world.credentials, pause=0)
    saturday = world.state.mailboxes["INBOX"].messages[1]
    deck = world.state.mailboxes["Sent"].messages[1]
    assert "To-reply" in saturday.flags and "Awaiting-reply" in deck.flags
    world.state.mailboxes["INBOX"].messages[4] = mail(7, "Dana <dana@example.net>", "reader@example.com",
                                                      "Re: Deck quote", "Here it is. Any questions?", 0, reply_to=2)
    connect.sync(world.worker(), world.sid)
    analysis.analyze_pending(world.worker())
    threads.track_pending(world.worker())
    moved = notify.reply_labels_pending(world.worker())[0]
    assert moved["removed"] == 1 and moved["added"] == 1  # Awaiting-reply off the sent message, To-reply on Dana's
    executor.run_pending(world.worker(), world.credentials, pause=0)
    assert "Awaiting-reply" not in deck.flags and "To-reply" in world.state.mailboxes["INBOX"].messages[4].flags
    notify.set_reply_labels(world.store, world.sid, False)
    assert notify.reply_labels_pending(world.worker())[0]["removed"] == 2
    executor.run_pending(world.worker(), world.credentials, pause=0)
    assert "To-reply" not in saturday.flags  # turning the labels off takes them off again
