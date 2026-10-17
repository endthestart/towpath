"""Finding and cleaning up mail: search (including archived mail), space, clean-up to the Archive folder, archives
to a file that are verified before anything leaves the server, and unsubscribing. Synthetic server and stand-ins."""

import hashlib
import sys
from pathlib import Path

import pytest
from django.test import Client, override_settings

from towpath import connect
from towpath.mail import analysis, archive, executor, search, senders, settings, unsubscribe
from towpath.stores import open_store
from towpath.web import auth
from towpath.web.application import configure

sys.path.insert(0, str(Path(__file__).parent))
import imap_stub as stub  # noqa: E402
from test_mail_threads import WRITE, World, mail  # noqa: E402

OWNER = "correct horse battery staple"


@pytest.fixture
def world(tmp_path, openai_stub):
    w = World(tmp_path, openai_stub)
    w.state.mailboxes["Trash"] = stub.Mailbox(5, {}, special="\\Trash")
    inbox = w.state.mailboxes["INBOX"].messages
    inbox[4] = mail(7, "Deals <deals@oneclick.example>", "reader@example.com", "Weekly deals", "From now on, deals.",
                    40, headers={"List-Unsubscribe": "<https://oneclick.example/u/9>, <mailto:u@oneclick.example>",
                                 "List-Unsubscribe-Post": "List-Unsubscribe=One-Click", "List-ID": "<deals>"})
    inbox[5] = mail(8, "Club <club@mailto.example>", "reader@example.com", "Club news", "Meeting notes.", 50,
                    headers={"List-Unsubscribe": "<mailto:leave@mailto.example?subject=leave>", "List-ID": "<club>"})
    connect.sync(w.worker(), w.sid)
    analysis.analyze_pending(w.worker())
    senders.categorize_pending(w.worker())
    yield w
    w.server.__exit__(None, None, None)


def go_live(w, *types):
    executor.password_path(w.credentials, w.sid).write_text(WRITE)
    settings.set_enabled_types(w.store, w.sid, set(types))
    settings.set_dry_run(w.store, w.sid, False)


def test_search_finds_words_and_the_index_can_be_built_for_an_older_store(world):
    assert [r["subject"] for r in search.search(world.store, world.sid, "couch")] == ["Saturday?"]
    assert search.search(world.store, world.sid, "cou*")[0]["where"] == "server"
    assert search.search(world.store, world.sid, 'couch" OR "x') == []  # typed quotes are never search syntax
    with open_store(world.store, "mail", "connect") as db:
        db.execute("INSERT INTO messages_text (messages_text) VALUES ('delete-all')")
        db.commit()
    assert search.search(world.store, world.sid, "couch") == []
    assert analysis.build_search(world.worker())[0]["step"] == "mail-search-index"
    assert len(search.search(world.store, world.sid, "couch")) == 1
    assert analysis.build_search(world.worker()) == []  # once only


def test_space_is_measured_by_year_folder_and_category(world):
    found = senders.space(world.store, world.sid)
    assert found["total"][0] == 8 and found["total"][1] > 0
    assert {b[0] for b in found["by_mailbox"]} == {"INBOX", "Sent"}
    assert any(c["category"] == "person" for c in found["by_category"])


def test_mbox_escaping_round_trips_and_verification_catches_a_changed_copy(tmp_path):
    raw = b"Subject: x\r\nMessage-ID: <m@x>\r\n\r\nFrom the start\r\n>From quoted\r\nend"
    assert archive.unescape(archive.escape(raw)) == raw
    path = tmp_path / "a.mbox"
    offset, length = archive.append(path, raw)
    sha = hashlib.sha256(raw).hexdigest()
    assert archive.verify(path, offset, length, sha, len(raw), "<m@x>") is None
    assert archive.verify(path, offset, length, sha, len(raw), "<other@x>") == "the copy's Message-ID differs"
    data = bytearray(path.read_bytes())
    data[offset + 3] ^= 1
    path.write_bytes(bytes(data))
    assert archive.verify(path, offset, length, sha, len(raw), "<m@x>") == "the copy's bytes differ"


def test_archive_to_a_file_then_remove_only_verified_copies(world):
    old = [m for m in _inbox(world) if m["subject"] in ("Weekly deals", "Club news")]
    rid = archive.request(world.store, world.sid, [m["item_id"] for m in old], True, "old lists")
    archive.archive_pending(world.worker())
    progress = archive.progress(world.store, rid)
    assert progress["archived"] == 2
    path = Path(progress["file"])
    assert path.parent == archive.root(world.store) / world.sid and path.stat().st_size > 0
    assert b"Weekly deals" in path.read_bytes() and b"Club news" in path.read_bytes()
    assert world.state.writes == [] and world.state.violations == []  # read with PEEK; nothing changed
    executor.run_pending(world.worker(), world.credentials, pause=0)  # approved by the request; dry runs only
    assert {r["outcome"] for r in executor.receipts(world.store)} == {"dry-run"}
    go_live(world, "trash")
    pids = [r["key"] for r in executor.receipts(world.store)]
    with open_store(world.store, "mailact", "act") as db:  # let the runner try them again, live this time
        db.executemany("DELETE FROM receipts WHERE key = ?", [(p,) for p in pids])
        db.commit()
    executor.run_pending(world.worker(), world.credentials, pause=0)
    assert len(world.state.mailboxes["INBOX"].messages) == 3
    assert {r["outcome"] for r in executor.receipts(world.store)} == {"done"}
    connect.sync(world.worker(), world.sid)
    assert search.search(world.store, world.sid, "deals")[0]["where"] == "archive"


def test_a_trash_proposal_without_a_verified_copy_isnt_approved(world):
    from towpath.mail import actions

    m = next(m for m in _inbox(world) if m["subject"] == "Club news")
    rid = archive.request(world.store, world.sid, [m["item_id"]], True, "x")
    body = actions.make(world.sid, "trash", actions.message_target(m), {}, producer=f"archive:{rid}")
    assert not archive.auto_approved(world.store, body)  # not archived yet


def test_unsubscribe_options_and_mechanics():
    found = unsubscribe.options("<https://a.example/u>, <mailto:x@a.example>", "List-Unsubscribe=One-Click")
    assert unsubscribe.best(found) == ("post", "https://a.example/u")
    assert unsubscribe.best(unsubscribe.options("<https://a.example/page>", None)) is None
    assert unsubscribe.options("<https://a.example/page>", None)["page"] == "https://a.example/page"
    with pytest.raises(unsubscribe.UnsubscribeError, match="public"):
        unsubscribe.one_click("https://127.0.0.1/u")

    sent = []

    class Opener:
        def open(self, request, timeout):
            sent.append((request.full_url, request.data, request.get_method()))

            class R:
                status = 200

                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False
            return R()

    assert unsubscribe.one_click("https://a.example/u", opener=Opener()) == "a.example answered 200"
    assert sent == [("https://a.example/u", b"List-Unsubscribe=One-Click", "POST")]
    m = unsubscribe.mail_message("mailto:leave@x.example?subject=leave%20now", "me@example.com")
    assert m["To"] == "leave@x.example" and m["Subject"] == "leave now"


def test_the_pages_clean_up_and_unsubscribe_as_dry_runs(world, monkeypatch):
    monkeypatch.setattr(auth, "_setup_codes", {})
    configure(world.store)
    auth.create_account(world.store, "owner", OWNER)
    calls = []
    monkeypatch.setattr(unsubscribe, "one_click", lambda url, opener=None: calls.append(url) or "ok")
    with override_settings(TOWPATH_STORE_DIR=world.store, TOWPATH_CONFIG=world.config, ALLOWED_HOSTS=["testserver"]):
        client = Client()
        client.post("/login", {"username": "owner", "password": OWNER})
        assert "couch" in client.get(f"/mail/{world.sid}/search?q=couch").content.decode()
        assert "Where the space goes" in client.get(f"/mail/{world.sid}/space").content.decode()
        page = client.get(f"/mail/{world.sid}/clean?mailbox=INBOX&older_than=30").content.decode()
        assert "2 messages" in page and "Weekly deals" in page
        client.post(f"/mail/{world.sid}/clean", {"mailbox": "INBOX", "older_than": "30", "do": "archive"})
        page = client.get(f"/mail/{world.sid}/unsubscribe").content.decode()
        assert "One click" in page and "By email" in page and "Their page" in page
        client.post(f"/mail/{world.sid}/unsubscribe/ask", {"address": ["deals@oneclick.example"],
                                                          "archive_future": "1"})
        assert "Asked" in client.get(f"/mail/{world.sid}/unsubscribe").content.decode()
        executor.run_pending(world.worker(), world.credentials, pause=0)
        got = executor.receipts(world.store)
        assert {r["outcome"] for r in got} == {"dry-run"} and calls == []
        assert any("one-click unsubscribe request to oneclick.example" in r["detail"] for r in got)
        from towpath.mail import rules

        assert rules.all_rules(world.store, world.sid, "web")[0]["mode"] == "auto"
        go_live(world, "unsubscribe")
        client.post(f"/mail/{world.sid}/unsubscribe/ask", {"address": ["deals@oneclick.example"]})
        executor.run_pending(world.worker(), world.credentials, pause=0)
        assert calls == ["https://oneclick.example/u/9"]
        assert "Archives" in client.get(f"/mail/{world.sid}/archives").content.decode()


def _inbox(world):
    with open_store(world.store, "mail", "web") as db:
        return [dict(r) for r in db.execute("SELECT * FROM messages WHERE mailbox = 'INBOX'")]
