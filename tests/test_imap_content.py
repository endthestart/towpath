"""The read-only IMAP adapter: selected content, provider search, MIME structure, and configuration."""

import json
from pathlib import Path

import pytest

from towpath import config as config_mod
from towpath import connect
from towpath.adapters import build_connector
from towpath.adapters.errors import NotFound
from towpath.adapters.imap import flatten_structure
from towpath.stores import open_store
from towpath.unified.contracts import parse_imap_native

from tests.imap_env import NEF, Env, make_env, stub


@pytest.fixture
def env(tmp_path, monkeypatch):
    yield from make_env(tmp_path, monkeypatch)


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
