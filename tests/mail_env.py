"""A synthetic IMAP account with managed mail and an action runner, for mail management's tests."""

from email.message import EmailMessage
from email import policy
import sys
from pathlib import Path

from towpath import config as config_mod
from towpath import connect
from towpath.mail import actions, analysis, executor, settings, watch
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


def make_env(tmp_path, monkeypatch):
    e = Env(tmp_path, monkeypatch)
    yield e
    watch.stop_all()
    e.server.__exit__(None, None, None)
