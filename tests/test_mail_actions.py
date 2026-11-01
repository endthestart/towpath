"""The action runner: dry runs, what may be turned on, approvals, receipts and undo, and its allow-list."""

import json

import pytest

from tests.mail_env import SID, WRITE_PASSWORD, make_env, reply
from towpath.canonical import digest
from towpath.mail import actions, executor, settings
from towpath.mail.executor import ActSession, WriteViolation
from towpath.stores import open_store


@pytest.fixture
def env(tmp_path, monkeypatch):
    yield from make_env(tmp_path, monkeypatch)


def test_a_requested_change_is_a_dry_run_until_its_type_is_turned_on(env):
    env.index()
    env.manage()
    env.analyze()
    env.give_write_password()
    env.ask("mark_read", "Lunch on Friday?")
    env.run()
    receipt = env.receipts()["req_000001"]
    assert receipt["outcome"] == "dry-run" and receipt["detail"].startswith("Dry run: would mark as read")
    assert "\\Seen" not in env.state.mailboxes["INBOX"].messages[1].flags
    assert env.state.writes == []
    env.run()  # one receipt per request, never repeated
    assert len(env.receipts()) == 1


def test_dry_runs_only_overrides_turned_on_types(env):
    env.index()
    env.manage()
    env.analyze()
    env.give_write_password()
    settings.set_enabled_types(env.store, SID, {"mark_read"})
    assert settings.dry_run(env.store, SID)  # on until the owner turns it off
    env.ask("mark_read", "Lunch on Friday?")
    env.run()
    assert env.receipts()["req_000001"]["outcome"] == "dry-run" and env.state.writes == []
    settings.set_dry_run(env.store, SID, False)
    env.ask("mark_read", "Lunch on Friday?")
    env.run()
    assert env.receipts()["req_000002"]["outcome"] == "done"


def test_without_the_write_password_a_turned_on_type_is_still_a_dry_run(env):
    env.index()
    env.manage()
    env.analyze()
    settings.set_enabled_types(env.store, SID, {"mark_read"})
    settings.set_dry_run(env.store, SID, False)
    env.ask("mark_read", "Lunch on Friday?")
    env.run()
    assert env.receipts()["req_000001"]["outcome"] == "dry-run"
    assert env.state.writes == []


def test_turned_on_changes_are_carried_out_with_receipts_and_undo(env):
    env.index()
    env.manage()
    env.analyze()
    env.give_write_password()
    settings.set_enabled_types(env.store, SID, {"mark_read", "archive", "move"})
    settings.set_dry_run(env.store, SID, False)
    env.ask("mark_read", "Lunch on Friday?")
    env.ask("archive", "October offers")
    env.run()
    got = env.receipts()
    assert got["req_000001"]["outcome"] == "done" and got["req_000002"]["outcome"] == "done"
    assert "\\Seen" in env.state.mailboxes["INBOX"].messages[1].flags
    assert 3 not in env.state.mailboxes["INBOX"].messages
    assert len(env.state.mailboxes["Archive"].messages) == 1
    assert got["req_000002"]["after"]["mailbox"] == "Archive"
    # Undo the archive: found again by Message-ID, since it has a new UID in Archive.
    undo = got["req_000002"]["inverse"]
    assert undo["type"] == "move" and undo["target"] == {"mailbox": "Archive", "message_id": "<msg-3@example.org>"}
    settings.ask(env.store, SID, undo)
    env.run()
    assert env.receipts()["req_000003"]["outcome"] == "done"
    assert len(env.state.mailboxes["Archive"].messages) == 0
    assert any(b"<msg-3@example.org>" in m.raw for m in env.state.mailboxes["INBOX"].messages.values())
    assert not any(w.startswith(("EXPUNGE", "CLOSE", "DELETE", "COPY")) for w in env.state.writes)


def test_a_change_to_a_message_that_moved_is_skipped(env):
    env.index()
    env.manage()
    env.analyze()
    env.give_write_password()
    settings.set_enabled_types(env.store, SID, {"flag"})
    settings.set_dry_run(env.store, SID, False)
    env.ask("flag", "Lunch on Friday?")
    box = env.state.mailboxes["INBOX"]
    env.state.mailboxes["Archive"].messages[1] = box.messages.pop(1)  # moved on the phone meanwhile
    env.run()
    receipt = env.receipts()["req_000001"]
    assert receipt["outcome"] == "skipped" and "no longer in that mailbox" in receipt["detail"]
    assert env.state.writes == []


def test_proposals_run_only_with_an_approval_of_exactly_what_was_proposed(env):
    env.index()
    env.manage()
    env.analyze()
    env.give_write_password()
    settings.set_enabled_types(env.store, SID, {"flag"})
    body = actions.make(SID, "flag", actions.message_target(env.messages()["Lunch on Friday?"]), reason="test",
                        producer="rule:test")
    pid = actions.proposal_id(body)
    with open_store(env.store, "mailplan", "worker") as db:
        db.execute("INSERT INTO proposals VALUES (?,?,?,?,?)",
                   (pid, SID, json.dumps(body), digest(body), "2026-10-16T00:00:00+00:00"))
        db.commit()
    env.run()
    assert env.receipts() == {}  # not approved: nothing happens
    settings.answer(env.store, pid, "0" * 64, "approved")  # an approval of something else
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(executor.checks, "EXPIRES_DAYS", 10_000)
        env.run()
    assert env.receipts()[pid]["outcome"] == "refused"
    assert env.state.writes == []


def test_an_expired_proposal_is_not_carried_out(env):
    env.index()
    env.manage()
    env.analyze()
    body = actions.make(SID, "flag", actions.message_target(env.messages()["Lunch on Friday?"]))
    pid = actions.proposal_id(body)
    with open_store(env.store, "mailplan", "worker") as db:
        db.execute("INSERT INTO proposals VALUES (?,?,?,?,?)", (pid, SID, json.dumps(body), digest(body),
                                                                "2020-01-01T00:00:00+00:00"))
        db.commit()
    settings.answer(env.store, pid, digest(body), "approved")
    env.run()
    assert "too long after" in env.receipts()[pid]["detail"]


def test_a_draft_is_appended_to_drafts_and_undo_moves_it_to_trash(env):
    env.index()
    env.manage()
    env.give_write_password()
    settings.set_enabled_types(env.store, SID, {"draft", "trash"})
    settings.set_dry_run(env.store, SID, False)
    raw = reply(9, "Re: Lunch on Friday?", "Noon is perfect.").decode()
    settings.ask(env.store, SID, actions.make(SID, "draft", {"mailbox_role": "drafts"}, {"message": raw}))
    env.run()
    receipt = env.receipts()["req_000001"]
    assert receipt["outcome"] == "done" and receipt["after"]["mailbox"] == "Drafts"
    drafts = env.state.mailboxes["Drafts"].messages
    assert len(drafts) == 1 and next(iter(drafts.values())).flags == {"\\Draft", "\\Seen"}
    settings.ask(env.store, SID, receipt["inverse"])
    env.run()
    assert env.receipts()["req_000002"]["outcome"] == "done"
    assert len(env.state.mailboxes["Trash"].messages) == 1 and not drafts


def test_the_write_session_offers_only_allowed_commands():
    class Client:
        def capabilities(self):
            return [b"IMAP4rev1"]

        def add_flags(self, *a):
            raise AssertionError("must not be reached")

    session = ActSession(Client())
    for flag in ("\\Deleted", "\\Draft", "bad label!"):
        with pytest.raises(WriteViolation):
            session.store(1, "+", flag)
    with pytest.raises(WriteViolation):
        session.move(1, "Archive")  # no MOVE capability: never copy and expunge instead
    public = {n for n in dir(session) if not n.startswith("_") and callable(getattr(session, n))}
    assert public == {"login", "capabilities", "folders", "special", "select", "read", "find", "store", "move",
                      "append", "unselect", "logout"}


def test_the_write_password_must_differ_from_the_read_password(env, tmp_path):
    from towpath import connections

    store, creds = tmp_path / "s", tmp_path / "c"
    settings_ = {"host": "127.0.0.1", "port": env.server.port, "security": "plain-loopback",
                 "username": env.state.username}
    sid = connections.add_imap(store, creds, "fastmail", settings_, WRITE_PASSWORD,
                               connections.test_imap(settings_, env.state.password))
    with pytest.raises(connections.ConnectionProblem, match="same password"):
        connections.save_write_password(store, creds, sid, WRITE_PASSWORD, {})


def test_validation_refuses_malformed_changes():
    with pytest.raises(actions.ActionError):
        actions.make(SID, "delete", {"mailbox": "INBOX", "uid": 1, "uidvalidity": 1})
    with pytest.raises(actions.ActionError):
        actions.make(SID, "flag", {"mailbox": "INBOX"})
    with pytest.raises(actions.ActionError):
        actions.make(SID, "draft", {"mailbox_role": "drafts"}, {"message": "Subject: no id\n\nhi"})
    assert not actions.TYPES["trash"]["auto"]
