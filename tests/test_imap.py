"""Read-only IMAP connector against a synthetic IMAP server: identity, reconciliation, no side effects."""

import json
import re
import sys
from pathlib import Path

import pytest

from towpath import config as config_mod
from towpath import connect
from towpath.adapters import build_connector
from towpath.adapters.errors import NotFound
from towpath.adapters.imap import ReadOnlySession, ReadOnlyViolation, flatten_structure
from towpath.stores import open_store
from towpath.unified.contracts import parse_imap_native

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


@pytest.fixture
def env(tmp_path, monkeypatch):
    e = Env(tmp_path, monkeypatch)
    yield e
    e.close()


def test_full_sync_indexes_every_selectable_mailbox_read_only(env):
    flags_before = env.state.flags()
    result = env.sync()
    assert result["termination"] == "complete" and result["complete"] and result["indexed_total"] == 3
    items = env.items()
    identities = {parse_imap_native(n) for n in items}
    assert identities == {("INBOX", 1700000001, 1), ("INBOX", 1700000001, 2), ("Archive/2008", 1700000002, 3)}
    subjects = {i["subject"] for i in items.values()}
    assert "Grüße vom Kanal" in subjects
    env.assert_read_only()
    assert env.state.flags() == flags_before  # \Seen neither added nor removed
    assert any(c.startswith("EXAMINE") for c in env.commands())
    assert not any(c.startswith("EXAMINE \"Projects\"") for c in env.commands())  # \Noselect skipped


def test_parts_carry_imap_numbering_filenames_and_no_bodies(env):
    env.sync()
    db = open_store(env.config.store_dir, "source", "web")
    rows = [dict(r) for r in db.execute("SELECT p.* FROM parts p JOIN items i USING (item_id) "
                                        "WHERE i.native_id LIKE 'INBOX;%UID=2' ORDER BY part_id")]
    db.close()
    assert [(r["part_id"], r["mime_type"], r["filename"], r["disposition"]) for r in rows] == [
        ("1", "text/plain", None, None), ("2", "image/x-nikon-nef", "DSC_0042.NEF", "attachment")]
    assert all(r["sha256"] is None for r in rows)  # nothing fetched while indexing


def test_second_run_reads_only_new_messages_and_marks_expunged_absent(env):
    env.sync()
    env.state.transcript.clear()
    inbox = env.state.mailboxes["INBOX"]
    inbox.messages[4] = stub.Message(stub.message(4, "New minutes", "Fresh text."))
    del inbox.messages[1]
    result = env.sync()
    assert result["termination"] == "complete" and result["new"] == 1 and result["absent"] == 1
    fetches = [c for c in env.commands() if "FETCH" in c]
    assert fetches == ["UID FETCH 4 (RFC822.SIZE INTERNALDATE BODYSTRUCTURE "
                       "BODY.PEEK[HEADER.FIELDS (DATE SUBJECT FROM MESSAGE-ID)])"]
    present = {parse_imap_native(n)[2] for n in env.present()}
    assert present == {2, 3, 4}
    env.assert_read_only()


def test_uidvalidity_change_never_maps_old_uids_onto_new_ones(env):
    env.sync()
    old = env.present()
    inbox = env.state.mailboxes["INBOX"]
    inbox.uidvalidity = 1800000000
    inbox.messages = {10: inbox.messages[2], 11: inbox.messages[1]}  # same bytes, renumbered
    result = env.sync()
    assert result["termination"] == "complete"
    now = env.present()
    assert {parse_imap_native(n) for n in old - now} == {("INBOX", 1700000001, 1), ("INBOX", 1700000001, 2)}
    assert {parse_imap_native(n) for n in now - old} == {("INBOX", 1800000000, 10), ("INBOX", 1800000000, 11)}
    assert len(env.items()) == 5  # the old identities remain as absent history, not rewritten


def test_deleted_or_renamed_mailbox_marks_only_its_items_absent(env):
    env.sync()
    env.state.mailboxes["Archive/Old"] = env.state.mailboxes.pop("Archive/2008")
    env.sync()
    present = {parse_imap_native(n)[:2] for n in env.present()}
    assert ("Archive/2008", 1700000002) not in present and ("Archive/Old", 1700000002) in present
    assert len([n for n in env.present() if n.startswith("INBOX")]) == 2


def test_a_mailbox_that_fails_to_open_is_never_reconciled_as_empty(env):
    env.sync()
    env.state.fail_examine = {"Archive/2008"}
    with pytest.raises(Exception):  # noqa: B017 - recorded as an error run, then re-raised
        env.sync()
    assert len(env.present()) == 3
    db = open_store(env.config.store_dir, "source", "web")
    last = db.execute("SELECT termination, complete FROM runs ORDER BY seq DESC LIMIT 1").fetchone()
    db.close()
    assert (last["termination"], last["complete"]) == ("error", 0)


def test_dropped_connection_stops_cleanly_and_the_next_run_resumes(env):
    env.state.drop_after_fetches = 1
    first = env.sync()
    assert first["termination"] == "server-stop" and not first["complete"]
    env.state.drop_after_fetches = None
    second = env.sync()
    assert second["termination"] == "complete" and len(env.present()) == 3
    assert len(env.items()) == 3


def test_capped_run_resumes_without_duplicates(env):
    first = env.sync(max_items=1)
    assert first["termination"] == "capped" and len(env.items()) == 1
    second = env.sync()
    assert second["termination"] == "complete" and len(env.items()) == 3 and len(env.present()) == 3


def test_fetch_batches_respect_server_limits(env, monkeypatch):
    env.state.max_fetch_ids = 1
    monkeypatch.setattr("towpath.adapters.imap.FETCH_BATCH", 1)
    env.config = config_mod.load(env.path)
    connector = build_connector(env.config.sources["imap-test"], env.config)
    connector.batch = 1
    events = list(connector.enumerate(None))
    connector.close()
    assert len([e for e in events if e[0] == "item"]) == 3


def test_server_granting_write_access_on_examine_is_refused(env):
    env.state.grant_read_write = True
    with pytest.raises(ReadOnlyViolation):
        env.sync()
    assert not any("FETCH" in c for c in env.commands())


def test_wrong_password_is_a_clean_auth_stop(env, monkeypatch):
    monkeypatch.setenv("TOWPATH_TEST_IMAP_PASSWORD", "not-the-password")
    result = env.sync()
    assert result["termination"] == "auth-stop" and env.items() == {}
    assert all("not-the-password" not in line for line in env.state.transcript)


def test_session_facade_refuses_state_changing_fetches_and_hides_the_client():
    class Client:
        normalise_times = True

        def fetch(self, uids, items):
            raise AssertionError("must not be reached")

    session = ReadOnlySession(Client())
    for item in ("BODY[1]", "BODY[]", "RFC822", "RFC822.TEXT", "BODY.PEEK[1] FLAGS"):
        with pytest.raises(ReadOnlyViolation):
            session.fetch([1], (item,))
    public = {n for n in dir(session) if not n.startswith("_")}
    assert public == {"login", "capabilities", "mailboxes", "folders", "examine", "search_all", "search_text",
                      "fetch", "logout"}


def test_selected_content_goes_through_the_queue_and_reads_with_peek(env):
    env.sync()
    flags_before = env.state.flags()
    items = env.items()
    nef = next(i for n, i in items.items() if n.endswith("UID=2"))
    text = next(i for n, i in items.items() if n.startswith("Archive"))
    queue = open_store(env.config.store_dir, "queue", "worker")
    for item, part in ((nef["item_id"], "2"), (text["item_id"], "1")):
        queue.execute("INSERT INTO content_requests (item_id, part_id, requested_by, priority, created_at) "
                      "VALUES (?, ?, 'test', 'interactive', '2026-10-06T00:00:00Z')", (item, part))
    queue.commit()
    queue.close()
    env.state.transcript.clear()
    result = connect.fetch_requests(env.config)
    assert result["fetched"] == 2 and result["failed"] == 0
    db = open_store(env.config.store_dir, "source", "web")
    cached = {r["bytes"] for r in db.execute("SELECT bytes FROM cache")}
    db.close()
    assert NEF in cached and "Grüße aus dem Kanalverein.\n".encode() in {c.replace(b"\r\n", b"\n") for c in cached}
    assert env.state.flags() == flags_before
    assert any("BODY.PEEK[2]" in c for c in env.commands())
    env.assert_read_only()


def test_fetching_from_a_reset_mailbox_identity_fails_without_substituting(env):
    env.sync()
    connector = build_connector(env.config.sources["imap-test"], env.config)
    native = next(n for n in env.items() if n.endswith("UID=2"))
    env.state.mailboxes["INBOX"].uidvalidity = 1900000000
    with pytest.raises(NotFound):
        connector.fetch(native, "2")
    connector.close()


def test_provider_search_pages_newest_first_across_mailboxes(env):
    connector = build_connector(env.config.sources["imap-test"], env.config)
    env.state.mailboxes["INBOX"].messages[5] = stub.Message(stub.message(5, "Kanal notes", "kanalverein ledger"))
    first = connector.search(["kanalverein"], None, 1)
    assert [parse_imap_native(r["native_id"]) for r in first["records"]] == [("Archive/2008", 1700000002, 3)]
    assert first["more"] is True
    second = connector.search(["kanalverein"], first["next_cursor"], 1)
    assert [parse_imap_native(r["native_id"])[2] for r in second["records"]] == [5]
    assert second["more"] is False and second["next_cursor"] is None
    utf8 = connector.search(["grüße"], None, 5)
    assert [r["subject"] for r in utf8["records"]] == ["Grüße vom Kanal"]
    connector.close()
    env.assert_read_only()


def test_provider_search_stops_when_identity_changes_between_pages(env):
    connector = build_connector(env.config.sources["imap-test"], env.config)
    first = connector.search(["the"], None, 1)
    assert first["more"]
    position = json.loads(first["next_cursor"])
    name = connector._mailboxes()[position["m"]]
    env.state.mailboxes[name].uidvalidity += 1
    with pytest.raises(NotFound):
        connector.search(["the"], first["next_cursor"], 1)
    connector.close()


def test_configured_mailboxes_missing_from_list_are_reported(tmp_path, monkeypatch):
    e = Env(tmp_path, monkeypatch, 'mailboxes = ["INBOX", "Gone"]')
    try:
        connector = build_connector(e.config.sources["imap-test"], e.config)
        report = connector.probe()
        connector.close()
        assert report["ok"] is False and report["missing_mailboxes"] == 1
        assert e.sync()["indexed_total"] == 2
    finally:
        e.close()


def test_body_structure_flattening_handles_nested_multiparts():
    leaf = (b"TEXT", b"PLAIN", (b"CHARSET", b"utf-8"), None, None, b"7BIT", 10, 1, None, None, None)
    att = (b"APPLICATION", b"PDF", (b"NAME", b"=?utf-8?q?Gr=C3=BC=C3=9Fe.pdf?="), None, None, b"BASE64", 99, None,
           (b"ATTACHMENT", None), None)
    nested = ([[leaf, leaf], b"ALTERNATIVE", None, None, None],)
    body = ([nested[0], att], b"MIXED", None, None, None)
    parts = flatten_structure(body)
    assert [(p["part_id"], p["depth"], p["mime_type"]) for p in parts] == [
        ("1.1", 2, "text/plain"), ("1.2", 2, "text/plain"), ("2", 1, "application/pdf")]
    assert parts[2]["filename"] == "Grüße.pdf" and parts[2]["disposition"] == "attachment"


@pytest.mark.parametrize("snippet,message", [
    ('security = "plain-loopback"\nhost = "imap.example.com"', "loopback"),
    ('security = "smoke-signals"', "security must be"),
    ('mailboxes = "INBOX"', "mailboxes must be"),
    ('port = 0', "port must be"),
    ('flags = "\\\\Seen"', "unknown key"),
])
def test_imap_configuration_is_strict(tmp_path, snippet, message):
    lines = {"host": 'host = "127.0.0.1"', "security": 'security = "plain-loopback"'}
    for key in list(lines):
        if snippet.startswith(key) or f"\n{key}" in snippet:
            lines.pop(key)
    path = tmp_path / "towpath.toml"
    path.write_text(f"""
[[sources]]
id = "imap-x"
kind = "mail-provider"
adapter = "imap"
username = "reader@example.com"
password = "env:NOPE"
{chr(10).join(lines.values())}
{snippet}
""")
    with pytest.raises(config_mod.ConfigError, match=message):
        config_mod.load(path)


def test_tls_is_the_default_with_the_standard_port(tmp_path):
    path = tmp_path / "towpath.toml"
    path.write_text("""
[[sources]]
id = "imap-x"
kind = "mail-provider"
adapter = "imap"
host = "imap.example.com"
username = "reader@example.com"
password = "env:NOPE"
""")
    src = config_mod.load(path).sources["imap-x"]
    assert (src.security, src.port, src.mailboxes) == ("tls", 993, None)


def test_commented_example_source_is_valid_when_uncommented(tmp_path):
    text = (Path(__file__).resolve().parents[1] / "examples" / "towpath.example.toml").read_text()
    start = text.index("# [[sources]]\n# id = \"imap_personal\"")
    block = []
    for line in text[start:].splitlines():
        if not line.startswith("#"):
            break
        block.append(line[2:].split("   #")[0].split("  #")[0])
    path = tmp_path / "towpath.toml"
    path.write_text("\n".join(block) + "\n")
    src = config_mod.load(path).sources["imap_personal"]
    assert (src.adapter, src.security, src.port, src.mailboxes) == ("imap", "tls", 993, ("INBOX", "Archive"))
