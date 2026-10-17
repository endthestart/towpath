"""Guards on the code that changes or removes real mail: Trash only as the owner allows, unsubscribing only to the
sender's own service, archives that can be asked for twice, and changes whose outcome is unknown never repeated."""

import sys
from pathlib import Path

import pytest

from towpath.adapters.errors import ServerStop
from towpath.mail import actions, archive, executor, rules, settings, unsubscribe

sys.path.insert(0, str(Path(__file__).parent))
from test_mail_cleanup import _inbox, go_live, world  # noqa: E402,F401


def test_a_move_into_trash_needs_the_trash_switch_and_the_owners_own_approval(world):  # noqa: F811
    with pytest.raises(rules.RuleError, match="Trash"):
        rules.save(world.store, world.sid, {"name": "Bin it", "conditions": {"mailbox": "INBOX", "from": "*"},
                                            "actions": [{"type": "move", "params": {"mailbox": "Trash"}}],
                                            "scope": "new"}, mode="auto")
    go_live(world, "move")  # move on, Trash off
    deals = next(m for m in _inbox(world) if m["subject"] == "Weekly deals")
    settings.ask(world.store, world.sid, actions.make(world.sid, "move", actions.message_target(deals),
                                                      {"mailbox": "Trash"}, reason="test"))
    executor.run_pending(world.worker(), world.credentials, pause=0)
    receipt = executor.receipts(world.store)[0]
    assert receipt["outcome"] == "skipped" and "Move to Trash" in receipt["detail"]
    assert not world.state.mailboxes["Trash"].messages


def test_a_bad_unsubscribe_is_recorded_and_the_queue_goes_on(world):  # noqa: F811
    with pytest.raises(actions.ActionError, match="control"):
        actions.make(world.sid, "unsubscribe", {"sender": "deals@oneclick.example"},
                     {"method": "mail", "url": "mailto:u@oneclick.example?subject=x%0d%0aBcc:a@b\r\n"})
    with pytest.raises(unsubscribe.UnsubscribeError):
        unsubscribe.one_click("https://a.example:8443/u")
    with pytest.raises(unsubscribe.UnsubscribeError, match="sender's own domain"):
        unsubscribe.mail_message("mailto:anyone@elsewhere.example", "me@example.com", list_from="news@shop.example")
    assert unsubscribe.mail_message("mailto:leave@lists.shop.example", "me@example.com",
                                    list_from="news@shop.example")["To"] == "leave@lists.shop.example"
    assert unsubscribe.best({"mail": "mailto:x@elsewhere.example"}, "news@shop.example") is None

    class Redirect:
        def open(self, request, timeout):
            class R:
                status = 302

                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False
            return R()

    with pytest.raises(unsubscribe.UnsubscribeError, match="302"):
        unsubscribe.one_click("https://a.example/u", opener=Redirect())


def test_archiving_the_same_mail_again_with_removal_proposes_the_removal(world):  # noqa: F811
    old = [m["item_id"] for m in _inbox(world) if m["subject"] in ("Weekly deals", "Club news")]
    archive.request(world.store, world.sid, old, False, "keep a copy")
    archive.archive_pending(world.worker())
    second = archive.request(world.store, world.sid, old, True, "and remove them")
    archive.archive_pending(world.worker())  # no crash on the second copy
    waiting = [p for p in executor.pending(world.store) if p["body"]["producer"] == f"archive:{second}"]
    assert len(waiting) == 2 and all(p["auto"] for p in waiting)
    moved = dict(waiting[0]["body"], target={**waiting[0]["body"]["target"], "uid": 999})
    assert not archive.auto_approved(world.store, moved)  # aimed at another message: not approved


def test_a_changed_archive_file_stops_the_removal(world):  # noqa: F811
    old = [m["item_id"] for m in _inbox(world) if m["subject"] == "Weekly deals"]
    rid = archive.request(world.store, world.sid, old, True, "remove")
    archive.archive_pending(world.worker())
    path = Path(archive.progress(world.store, rid)["file"])
    path.write_bytes(path.read_bytes().replace(b"deals", b"DEALS"))
    go_live(world, "trash")
    executor.run_pending(world.worker(), world.credentials, pause=0)
    receipt = executor.receipts(world.store)[0]
    assert receipt["outcome"] == "skipped" and "differ" in receipt["detail"]
    assert not world.state.mailboxes["Trash"].messages


def test_a_dropped_connection_after_the_command_is_recorded_not_repeated(world, monkeypatch):  # noqa: F811
    go_live(world, "flag")
    deals = next(m for m in _inbox(world) if m["subject"] == "Weekly deals")
    settings.ask(world.store, world.sid, actions.make(world.sid, "flag", actions.message_target(deals), {},
                                                      reason="test"))

    def dropped(act, body, trash_ok=True):
        act.sent = True
        raise ServerStop("the connection dropped")

    monkeypatch.setattr(executor, "_execute", dropped)
    executor.run_pending(world.worker(), world.credentials, pause=0)
    assert executor.receipts(world.store)[0]["outcome"] == "uncertain"
    assert executor.pending(world.store) == []  # not tried again


def test_keywords_compare_without_case_and_mbox_lines_split_at_lf_only():
    body = actions.make("s", "add_keyword", {"item_id": "i", "mailbox": "INBOX", "uidvalidity": 1, "uid": 2},
                        {"keyword": "Receipt"})
    assert actions.inverse(body, {"flags": ["receipt"]}, {}) is None  # it was there already: Undo leaves it
    raw = b"Subject: x\r\n\r\nline\rFrom here stays\nFrom the start\n"
    assert archive.escape(raw) == b"Subject: x\r\n\r\nline\rFrom here stays\n>From the start\n"
    assert archive.unescape(archive.escape(raw)) == raw

