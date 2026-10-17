"""A Facebook export as a source: posts and day-by-day conversations indexed and readable, Facebook's mojibake undone,
the owner's own words counted as likely life evidence, and the export never changed. Invented data only."""

import json
import sys
from pathlib import Path

import pytest

from towpath import config as config_mod, connect, connections, life_propose
from towpath.adapters.facebook import FacebookConnector, fix
from towpath.stores import open_store

sys.path.insert(0, str(Path(__file__).parent))


def mojibake(text: str) -> str:
    return text.encode("utf-8").decode("latin-1")


@pytest.fixture
def export(tmp_path, monkeypatch):
    library = tmp_path / "library"
    root = library / "facebook-2019"
    (root / "profile_information").mkdir(parents=True)
    (root / "profile_information" / "profile_information.json").write_text(json.dumps(
        {"profile_v2": {"name": {"full_name": "Reader Example"}}}))
    (root / "your_facebook_activity" / "posts").mkdir(parents=True)
    (root / "your_facebook_activity" / "posts" / "your_posts__check_ins__photos_and_videos_1.json").write_text(
        json.dumps([{"timestamp": 1242300000,
                     "data": [{"post": mojibake("Grüße from the canal! We got married today.")}],
                     "attachments": [{"data": [{"place": {"name": "Example Junction"}}]}]}]))
    thread = root / "your_facebook_activity" / "messages" / "inbox" / "samrivera_123"
    thread.mkdir(parents=True)
    (thread / "message_1.json").write_text(json.dumps({
        "participants": [{"name": "Sam Rivera"}, {"name": "Reader Example"}], "title": "Sam Rivera",
        "messages": [{"sender_name": "Sam Rivera", "timestamp_ms": 1242300100000, "content": "Congratulations!!"},
                     {"sender_name": "Reader Example", "timestamp_ms": 1242300200000, "content": "Thank you Sam"},
                     {"sender_name": "Sam Rivera", "timestamp_ms": 1242400000000, "content": "Photos?"}]}))
    monkeypatch.setenv("TOWPATH_LIBRARY", str(library))
    (tmp_path / "towpath.toml").write_text('[stores]\ndir = "state"\n')
    config = config_mod.load(tmp_path / "towpath.toml")
    before = {p: p.read_bytes() for p in root.rglob("*.json")}
    yield config, tmp_path / "credentials", root, before


def test_fix_undoes_facebooks_mojibake():
    assert fix(mojibake("Grüße ❤️")) == "Grüße ❤️"
    assert fix("plain") == "plain" and fix("Grüße") == "Grüße"  # already right: left alone


def test_the_export_is_indexed_and_read(export):
    config, creds, root, before = export
    with pytest.raises(connections.ConnectionProblem):
        connections.add_facebook(config.store_dir, root / "profile_information")
    sid = connections.add_facebook(config.store_dir, root)
    connections.start_indexing(config.store_dir, creds, sid)
    connections.index_pending(config, creds)
    with open_store(config.store_dir, "source", "web") as db:
        items = {r["native_id"]: dict(r) for r in db.execute("SELECT * FROM items WHERE source_id = ?", (sid,))}
    assert len(items) == 3  # one post; two days of conversation with Sam
    post = next(v for k, v in items.items() if k.startswith("post:"))
    assert post["subject"].startswith("Grüße from the canal") and post["date_utc"].startswith("2009-05-14")
    chat = next(v for k, v in items.items() if k.startswith("chat:") and v["date_utc"].startswith("2009-05-14"))
    assert chat["subject"] == "Messages with Sam Rivera" and chat["from_addr"] == "Sam Rivera"
    text = FacebookConnector(sid, root).fetch(chat["native_id"], "1").decode()
    assert "Sam Rivera: Congratulations!!" in text and "Reader Example: Thank you Sam" in text
    assert "At Example Junction" in FacebookConnector(sid, root).fetch(post["native_id"], "1").decode()
    assert {p: p.read_bytes() for p in root.rglob("*.json")} == before
    worker = connections.merged(config, creds)
    assert worker.sources[sid].adapter == "facebook"
    found = life_propose._candidates(worker, {"source_id": sid, "after": "2009-01-01", "before": "2009-12-31"},
                                     set(), 10)
    assert {c["native_id"].split(":")[0] for c in found} == {"post", "chat"}  # the owner's own words are likely
    again = connect.sync(worker, sid)
    assert again["new"] == 0 and again["absent"] == 0
