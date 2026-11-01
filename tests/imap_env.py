"""A synthetic IMAP server and an indexed account on it, for the read-only IMAP adapter's tests."""

import re
import sys
from pathlib import Path

from towpath import config as config_mod
from towpath import connect
from towpath.stores import open_store

sys.path.insert(0, str(Path(__file__).parent))
import imap_stub as stub  # noqa: E402

NEF = b"\x4d\x4d\x00\x2a synthetic raw sensor bytes " * 4
WRITES = {"SELECT", "STORE", "COPY", "MOVE", "EXPUNGE", "APPEND", "CREATE", "DELETE", "RENAME", "SUBSCRIBE",
          "CLOSE", "UNSELECT"}


def mailboxes():
    return {
        "INBOX": stub.Mailbox(1700000001, {
            1: stub.Message(stub.message(1, "Towpath survey", "The lock keeper counted forty narrowboats."),
                            flags={"\\Seen"}),
            2: stub.Message(stub.message(2, "Photos from the aqueduct walk", "Raw file attached.",
                                         attachment=("DSC_0042.NEF", NEF, "image/x-nikon-nef"))),
        }),
        "Archive/2008": stub.Mailbox(1700000002, {
            3: stub.Message(stub.message(3, "=?utf-8?q?Gr=C3=BC=C3=9Fe_vom_Kanal?=", "Grüße aus dem Kanalverein.",
                                         date="Mon, 30 Jun 2008 20:00:00 +0000", charset_body=True)),
        }),
        "Projects": stub.Mailbox(1, {}, noselect=True),
    }


class Env:
    def __init__(self, tmp_path: Path, monkeypatch, mailboxes_line: str = ""):
        self.state = stub.State(mailboxes())
        self.server = stub.Server(self.state).__enter__()
        monkeypatch.setenv("TOWPATH_TEST_IMAP_PASSWORD", self.state.password)
        self.path = tmp_path / "towpath.toml"
        self.path.write_text(f"""
[stores]
dir = "state"

[[sources]]
id = "imap-test"
kind = "mail-provider"
adapter = "imap"
host = "127.0.0.1"
port = {self.server.port}
security = "plain-loopback"
username = "{self.state.username}"
password = "env:TOWPATH_TEST_IMAP_PASSWORD"
timeout_seconds = 5
{mailboxes_line}
""")
        self.config = config_mod.load(self.path)

    def sync(self, **kw):
        return connect.sync(self.config, "imap-test", **kw)

    def items(self) -> dict:
        db = open_store(self.config.store_dir, "source", "web")
        try:
            return {r["native_id"]: dict(r) for r in db.execute("SELECT * FROM items WHERE source_id = 'imap-test'")}
        finally:
            db.close()

    def present(self) -> set:
        return {k for k, v in self.items().items() if v["absent_since_run"] is None}

    def commands(self) -> list[str]:
        return [line.split(" ", 1)[1] for line in self.state.transcript]

    def assert_read_only(self):
        assert self.state.violations == []
        for command in self.commands():
            verb = command.split(" ")[1] if command.startswith("UID ") else command.split(" ")[0]
            assert verb.upper() not in WRITES, command
            if "FETCH" in command:
                assert not re.search(r"BODY\[|RFC822(\.TEXT)?(?![.A-Z])", command.replace("BODY.PEEK[", "")), command

    def close(self):
        self.server.__exit__(None, None, None)


def make_env(tmp_path, monkeypatch):
    e = Env(tmp_path, monkeypatch)
    yield e
    e.close()
