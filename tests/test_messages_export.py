"""Apple Messages from an imessage-exporter text export: read per conversation per day, never changed, and kept to
the same protections as mail. Made-up conversations only."""

import sys
from pathlib import Path

import pytest

from towpath import config as config_mod, connect, connections, life_propose
from towpath.adapters.messages_export import MessagesExportConnector, parse
from towpath.stores import open_store

sys.path.insert(0, str(Path(__file__).parent))

SAM = """May 14, 2009  7:15:00 PM
Sam Rivera
Congratulations!!
So happy for you both.

May 14, 2009  7:20:10 PM (Read by you after 2 minutes)
Me
Thank you Sam

May 15, 2009  9:01:00 AM
Sam Rivera
Photos?
"""


@pytest.fixture
def export(tmp_path, monkeypatch):
    library = tmp_path / "library"
    root = library / "messages-export"
    root.mkdir(parents=True)
    (root / "Sam Rivera.txt").write_text(SAM)
    (root / "+15550001111.txt").write_text("Jun 01, 2010  10:00:00 AM\n+15550001111\nYour code is 123456\n")
    (library / "notes").mkdir()
    (library / "notes" / "todo.txt").write_text("buy milk\n")
    monkeypatch.setenv("TOWPATH_LIBRARY", str(library))
    (tmp_path / "towpath.toml").write_text('[stores]\ndir = "state"\n')
    config = config_mod.load(tmp_path / "towpath.toml")
    before = {p: p.read_bytes() for p in root.rglob("*")}
    yield config, tmp_path / "credentials", root, before, library


def test_the_text_format_is_read_message_by_message():
    found = parse(SAM)
    assert [(w.isoformat()[:16], s) for w, s, _ in found] == [
        ("2009-05-15T00:15", "Sam Rivera"), ("2009-05-15T00:20", "Me"), ("2009-05-15T14:01", "Sam Rivera")]
    assert found[0][2] == "Congratulations!!\nSo happy for you both."  # a message over several lines


def test_the_export_is_indexed_and_read_and_never_changed(export):
    config, creds, root, before, library = export
    with pytest.raises(connections.ConnectionProblem):
        connections.add_messages(config.store_dir, library / "notes")
    sid = connections.add_messages(config.store_dir, root)
    connections.start_indexing(config.store_dir, creds, sid)
    connections.index_pending(config, creds)
    with open_store(config.store_dir, "source", "web") as db:
        items = {r["native_id"]: dict(r) for r in db.execute("SELECT * FROM items WHERE source_id = ?", (sid,))}
    assert len(items) == 3  # two days with Sam (local time), one with a short code number
    chat = next(v for v in items.values() if v["subject"] == "Messages with Sam Rivera" and
                "SENT" in open_store(config.store_dir, "source", "web").execute(
                    "SELECT labels FROM observations WHERE item_id = ?", (v["item_id"],)).fetchone()[0])
    assert chat["from_addr"] == "Sam Rivera"
    text = MessagesExportConnector(sid, root).fetch(chat["native_id"], "1").decode()
    assert "Sam Rivera: Congratulations!!" in text and "Me: Thank you Sam" in text
    assert {p: p.read_bytes() for p in root.rglob("*")} == before
    worker = connections.merged(config, creds)
    assert worker.sources[sid].adapter == "imessage"
    found = life_propose._candidates(worker, {"source_id": sid, "after": "2009-01-01", "before": "2009-12-31"},
                                     set(), 10)
    assert any("Sam Rivera" in c["native_id"] for c in found)  # the owner wrote: likely
    assert connect.sync(worker, sid)["new"] == 0


def test_a_kept_sender_is_named_to_the_gateway(export, monkeypatch, openai_stub):
    from towpath import decisions
    from towpath.models import gateway

    config, creds, root, _, _ = export
    stub = openai_stub(model="local-35b")
    form = {"base_url": f"http://127.0.0.1:{stub.port}/v1", "model": "local-35b"}
    model = connections.add_model(config.store_dir, creds, form, "t", connections.test_model(form, "t"))
    decisions.grant_model(connections.merged(config, creds), model, "content", author="owner")
    sid = connections.add_messages(config.store_dir, root)
    connections.start_indexing(config.store_dir, creds, sid)
    connections.index_pending(config, creds)
    from towpath.mail.privacy import sender_target

    seen = []
    monkeypatch.setattr(gateway, "infer", lambda c, t, p, d, s, items=(), **k: seen.append(list(items)) or
                        {"status": "refused", "reason": "item x is kept from cloud models and endpoint e is one"})
    from towpath.unified import grants

    grants.grant(config.store_dir, sid, "life-evidence", author="owner")
    life_propose.set_request(config.store_dir, sid, "2009-01-01", "2009-12-31", author="owner")
    worker = connections.merged(config, creds)
    monkeypatch.setattr(life_propose, "blocked", lambda *a, **k: None)
    life_propose.propose_pending(worker, creds)
    assert any(sender_target(sid, "Sam Rivera") in items for items in seen)


def test_the_add_page(export, monkeypatch):
    from django.test import Client, override_settings

    from towpath.web import auth
    from towpath.web.application import configure

    config, creds, root, _, _ = export
    monkeypatch.setattr(auth, "_setup_codes", {})
    configure(config.store_dir)
    auth.create_account(config.store_dir, "owner", "correct horse battery staple")
    with override_settings(TOWPATH_STORE_DIR=config.store_dir, TOWPATH_CONFIG=config, TOWPATH_CREDENTIALS_DIR=creds,
                           ALLOWED_HOSTS=["testserver"], ROOT_URLCONF="towpath.web.connection_urls"):
        client = Client()
        client.post("/login", {"username": "owner", "password": "correct horse battery staple"})
        assert "imessage-exporter -f txt" in client.get("/connections/add/imessage").content.decode()
        added = client.post("/connections/add/imessage", {"path": "messages-export"})
        assert added.status_code == 302
