"""Mail management foundation: reading managed mail, dry runs, the action runner's allow-list, receipts and Undo,
and the new-mail watcher, against a synthetic IMAP server that offers Fastmail's extensions."""

import json
import sys
import time
from email.message import EmailMessage
from email import policy
from pathlib import Path

import pytest

from towpath import config as config_mod
from towpath import connect
from towpath.canonical import digest
from towpath.mail import actions, analysis, executor, settings, text, watch
from towpath.mail.executor import ActSession, WriteViolation
from towpath.stores import open_store
from towpath.unified import grants

sys.path.insert(0, str(Path(__file__).parent))
import imap_stub as stub  # noqa: E402

SID = "fastmail"
WRITE_PASSWORD = "synthetic-write-only"


def reply(n: int, subject: str, body: str, in_reply_to: str | None = None, sender="friend@example.org",
          headers: dict | None = None, html: str | None = None) -> bytes:
    m = EmailMessage()
    m["From"] = f"A Friend <{sender}>"
    m["To"] = "reader@example.com"
    m["Subject"] = subject
    m["Date"] = f"Thu, 0{n % 9 + 1} Oct 2026 10:15:00 +0000"
    m["Message-ID"] = f"<msg-{n}@example.org>"
    if in_reply_to:
        m["In-Reply-To"] = in_reply_to
        m["References"] = in_reply_to
    for k, v in (headers or {}).items():
        m[k] = v
    m.set_content(body)
    if html:
        m.add_alternative(html, subtype="html")
    return m.as_bytes(policy=policy.SMTP)


def mailboxes():
    return {
        "INBOX": stub.Mailbox(41, {
            1: stub.Message(reply(1, "Lunch on Friday?", "Are you free on Friday?\n\nSee you")),
            2: stub.Message(reply(2, "Re: Lunch on Friday?", "Yes, noon works.\n\nOn Thu, Oct 1, 2026, A Friend "
                                  "wrote:\n> Are you free on Friday?", in_reply_to="<msg-1@example.org>"),
                            flags={"\\Seen"}),
            3: stub.Message(reply(3, "October offers", "Plain part", sender="news@shop.example",
                                  headers={"List-Unsubscribe": "<https://shop.example/u/1>, <mailto:u@shop.example>",
                                           "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
                                           "List-ID": "Shop news <news.shop.example>"},
                                  html="<p>Big <b>sale</b></p>")),
        }),
        "Archive": stub.Mailbox(42, {}, special="\\Archive"),
        "Trash": stub.Mailbox(43, {}, special="\\Trash"),
        "Drafts": stub.Mailbox(44, {}, special="\\Drafts"),
    }


class Env:
    def __init__(self, tmp_path: Path, monkeypatch):
        self.state = stub.State(mailboxes(), writable=True, write_password=WRITE_PASSWORD)
        self.server = stub.Server(self.state).__enter__()
        monkeypatch.setenv("TOWPATH_TEST_IMAP_PASSWORD", self.state.password)
        self.path = tmp_path / "towpath.toml"
        self.path.write_text(f"""
[stores]
dir = "state"

[[sources]]
id = "{SID}"
kind = "mail-provider"
adapter = "imap"
host = "127.0.0.1"
port = {self.server.port}
security = "plain-loopback"
username = "{self.state.username}"
password = "env:TOWPATH_TEST_IMAP_PASSWORD"
timeout_seconds = 5
mailboxes = ["INBOX", "Archive"]
""")
        self.config = config_mod.load(self.path)
        self.store = self.config.store_dir
        self.credentials = tmp_path / "credentials"
        self.credentials.mkdir()

    def manage(self):
        grants.grant(self.store, SID, "mail-manage", author="owner")

    def index(self):
        return connect.sync(self.config, SID)

    def analyze(self, **kw):
        return analysis.analyze_pending(self.config, **kw)

    def messages(self) -> dict:
        with open_store(self.store, "mail", "web") as db:
            return {r["subject"]: dict(r) for r in db.execute("SELECT * FROM messages")}

    def give_write_password(self):
        executor.password_path(self.credentials, SID).write_text(WRITE_PASSWORD)

    def run(self):
        return executor.run_pending(self.config, self.credentials, pause=0)

    def receipts(self):
        return {r["key"]: r for r in executor.receipts(self.store)}

    def ask(self, kind: str, subject: str, **params) -> int:
        record = self.messages()[subject]
        return settings.ask(self.store, SID, actions.make(SID, kind, actions.message_target(record), params))

    def read_only_commands(self) -> list[str]:
        """Commands sent with the read-only password: those of connections that logged in with it."""
        return self.state.transcript


@pytest.fixture
def env(tmp_path, monkeypatch):
    e = Env(tmp_path, monkeypatch)
    yield e
    watch.stop_all()
    e.server.__exit__(None, None, None)


# -- reading ---------------------------------------------------------------------------------------------------

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
    monkeypatch.setattr(analysis, "FLAGS_EVERY_SECONDS", 0)
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


# -- the action runner ------------------------------------------------------------------------------------------

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
        mp.setattr(executor, "EXPIRES_DAYS", 10_000)
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


# -- watching for new mail --------------------------------------------------------------------------------------

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
