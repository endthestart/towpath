"""Mail management foundation: reading managed mail, dry runs, the action runner's allow-list, receipts and Undo,
and the new-mail watcher, against a synthetic IMAP server that offers Fastmail's extensions."""

import json
import time

from towpath import config as config_mod
from towpath.mail import analysis, executor, settings, text, watch


import pytest

from tests.mail_env import SID, WRITE_PASSWORD, Env, mailboxes, make_env, reply, stub


@pytest.fixture
def env(tmp_path, monkeypatch):
    yield from make_env(tmp_path, monkeypatch)


def test_nothing_is_read_until_the_owner_turns_management_on(env):
    env.index()
    assert env.analyze() == []
    assert env.messages() == {}


def test_managed_mail_is_read_with_threads_list_headers_and_new_text_only(env):
    env.index()
    env.manage()
    flags_before = env.state.flags()
    summary = env.analyze()
    assert summary[0]["read"] == 3
    got = env.messages()
    lunch, re_lunch, offers = got["Lunch on Friday?"], got["Re: Lunch on Friday?"], got["October offers"]
    assert lunch["thread_key"] == re_lunch["thread_key"] != offers["thread_key"]
    assert lunch["thread_key"].startswith("t:")  # the server's THREADID (OBJECTID) is preferred
    assert re_lunch["text"] == "Yes, noon works." and re_lunch["quoted_removed"] == 1
    assert json.loads(re_lunch["flags"]) == ["\\Seen"]
    assert offers["list_unsubscribe_post"] == "List-Unsubscribe=One-Click"
    assert offers["from_addr"] == "news@shop.example" and lunch["from_name"] == "A Friend"
    assert offers["text"] == "Plain part"
    assert env.state.flags() == flags_before  # nothing was marked read
    assert env.state.violations == [] and env.state.writes == []
    assert not any(c.split(" ")[1] in ("SELECT", "STORE", "MOVE", "APPEND") for c in env.state.transcript)
    progress = analysis.progress(env.store, SID)
    assert progress["read"] == progress["indexed"] == 3 and progress["history_done"]


def test_new_mail_is_read_first_and_history_resumes_after_a_stop(env):
    env.index()
    env.manage()
    env.analyze(slice_seconds=-1)  # the deadline has already passed: only the flag check runs
    assert env.messages() == {}
    env.state.mailboxes["INBOX"].messages[4] = stub.Message(reply(4, "New one", "Just arrived"))
    env.index()
    env.analyze()
    assert set(env.messages()) == {"Lunch on Friday?", "Re: Lunch on Friday?", "October offers", "New one"}


def test_changed_flags_are_picked_up_with_condstore(env, monkeypatch):
    env.index()
    env.manage()
    env.analyze()
    monkeypatch.setattr(analysis.worker, "FLAGS_EVERY_SECONDS", 0)
    env.analyze()  # the first flag read of a mailbox reads every message's flags and remembers its MODSEQ
    msg = env.state.mailboxes["INBOX"].messages[1]
    msg.flags.add("\\Flagged")
    env.state.bump(msg)
    env.analyze()
    assert json.loads(env.messages()["Lunch on Friday?"]["flags"]) == ["\\Flagged"]
    assert any("CHANGEDSINCE" in c for c in env.state.transcript)


def test_one_message_that_cant_be_read_doesnt_stop_the_others(env, monkeypatch):
    env.index()
    env.manage()
    real = text.headers

    def picky(raw):
        if b"October offers" in raw:
            raise TypeError("synthetic")
        return real(raw)

    monkeypatch.setattr(text, "headers", picky)
    assert env.analyze()[0]["read"] == 3
    got = env.messages()
    assert {"Lunch on Friday?", "Re: Lunch on Friday?", None} == set(got)
    assert json.loads(got[None]["problems"]) == ["unreadable (TypeError)"]


def test_headers_with_raw_8bit_bytes_are_read_as_text():
    raw = (b"From: Jos\xe9 <jose@example.org>\r\nSubject: Caf\xe9\r\nMessage-ID: <a@x>\r\n"
           b"References: <r1@x> \xe9 <r2@x>\r\nIn-Reply-To: <r2@x>\r\nDate: Thu, 01 Oct 2026 10:00:00 +0000\r\n\r\n")
    fields = text.headers(raw)
    assert fields["refs"] == ["<r1@x>", "<r2@x>"] and fields["in_reply_to"] == "<r2@x>"
    assert fields["from_addr"] == "jose@example.org" and fields["subject"]


def test_quote_removal_and_html_text():
    text_, removed = text.without_quotes("Thanks!\n\nFrom: Someone\nSent: Monday\nTo: me\n\nold text")
    assert text_.strip() == "Thanks!" and removed
    html = text.clean(text.html_to_text("<p>One</p><blockquote>old</blockquote><p>Two &amp; three</p>"))
    assert "old" not in html and "[quoted]" in html and html.endswith("Two & three")


def test_the_watcher_asks_for_indexing_when_new_mail_arrives(env, monkeypatch):
    from towpath import connections

    calls = []
    monkeypatch.setattr(watch, "request_indexing", lambda store_dir, sid: calls.append(sid) or True)
    monkeypatch.setattr(connections, "get", lambda store_dir, sid, role="connect": {"state": "ready",
                                                                                    "indexing": "idle"})
    w = watch.Watcher(env.config, SID, idle_seconds=0.5, settle_seconds=0)
    w.start()
    deadline = time.monotonic() + 5
    while (watch.state(env.store, SID) or {}).get("state") != "watching" and time.monotonic() < deadline:
        time.sleep(0.05)
    with env.state.lock:
        env.state.idle_events.append("4 EXISTS")
    while not calls and time.monotonic() < deadline:
        time.sleep(0.05)
    w.stop()
    w.join(5)
    assert calls == [SID]
    assert env.state.writes == [] and env.state.violations == []


def test_gmail_over_imap_threads_labels_archive_and_undo(tmp_path, monkeypatch):
    """Gmail: threads from X-GM-THRID, labels instead of keywords, archive to All Mail, and nothing ever leaves All
    Mail except to Trash (in Gmail, taking a message out of All Mail deletes it)."""
    boxes = mailboxes()
    boxes["[Gmail]/All Mail"] = stub.Mailbox(45, {1: stub.Message(boxes["INBOX"].messages[3].raw)}, special="\\All")
    del boxes["Archive"]
    state = stub.State(boxes, writable=True, write_password=WRITE_PASSWORD, gmail=True)
    env = Env.__new__(Env)
    env.state, env.server = state, stub.Server(state).__enter__()
    try:
        monkeypatch.setenv("TOWPATH_TEST_IMAP_PASSWORD", state.password)
        env.path = tmp_path / "towpath.toml"
        env.path.write_text(f"""
[stores]
dir = "state"

[[sources]]
id = "{SID}"
kind = "mail-provider"
adapter = "imap"
host = "127.0.0.1"
port = {env.server.port}
security = "plain-loopback"
username = "{state.username}"
password = "env:TOWPATH_TEST_IMAP_PASSWORD"
timeout_seconds = 5
mailboxes = ["INBOX", "[Gmail]/All Mail"]
""")
        env.config = config_mod.load(env.path)
        env.store = env.config.store_dir
        env.credentials = tmp_path / "credentials"
        env.credentials.mkdir()
        env.index()
        env.manage()
        env.analyze()
        lunch = env.messages()["Lunch on Friday?"]
        assert lunch["thread_key"].startswith("t:gm")
        env.give_write_password()
        settings.set_enabled_types(env.store, SID, {"archive", "add_keyword", "move"})
        settings.set_dry_run(env.store, SID, False)
        env.ask("add_keyword", "Lunch on Friday?", keyword="Friends")
        env.ask("archive", "October offers")
        env.run()
        assert any(w.startswith("LABELS +X-GM-LABELS Friends") for w in state.writes)
        assert 3 not in state.mailboxes["INBOX"].messages  # archived: out of the inbox, still in All Mail
        undo = next(r for r in executor.receipts(env.store) if r["body"]["type"] == "archive")["inverse"]
        assert undo["target"]["mailbox"] == "[Gmail]/All Mail" and undo["params"]["mailbox"] == "INBOX"
        settings.ask(env.store, SID, undo)
        env.run()
        all_mail = state.mailboxes["[Gmail]/All Mail"].messages
        assert len(all_mail) == 1 and next(iter(all_mail.values())).labels == {"\\Inbox"}
        assert not any(w.startswith("MOVE [Gmail]/All Mail") for w in state.writes)
    finally:
        env.server.__exit__(None, None, None)
