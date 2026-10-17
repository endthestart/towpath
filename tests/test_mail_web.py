"""The Mail page and the write password, driven like the owner would, against the synthetic server with Fastmail's
extensions: manage an account, read a message, ask for changes (dry runs), add the write password, turn a kind of
change on, see it done, Undo it. The read-only password must never send a command that changes mail."""

import sys
from contextlib import closing
from pathlib import Path

import pytest
from django.test import Client, override_settings

from towpath import config as config_mod
from towpath import connect, connections
from towpath.mail import analysis, executor
from towpath.stores import open_store
from towpath.web import auth
from towpath.web.application import configure

sys.path.insert(0, str(Path(__file__).parent))
import imap_stub as stub  # noqa: E402
from test_connections import PASSWORD, mailboxes  # noqa: E402

OWNER = "correct horse battery staple"
WRITE_PASSWORD = "synthetic-write-password"


@pytest.fixture
def mail(tmp_path, monkeypatch):
    class env:  # noqa: N801 - a namespace for the test's parts
        pass

    boxes = mailboxes()
    boxes["Archive"].special = "\\Archive"
    env.state = stub.State(boxes, password=PASSWORD, writable=True, write_password=WRITE_PASSWORD)
    env.server = stub.Server(env.state).__enter__()
    env.credentials = tmp_path / "credentials"
    (tmp_path / "towpath.toml").write_text('[stores]\ndir = "state"\n')
    env.config = config_mod.load(tmp_path / "towpath.toml")
    env.store = env.config.store_dir
    env.settings = {"host": "127.0.0.1", "port": env.server.port, "security": "plain-loopback",
                    "username": env.state.username}
    sid = connections.add_imap(env.store, env.credentials, "fastmail", env.settings, PASSWORD,
                               connections.test_imap(env.settings, PASSWORD))
    connections.choose_folders(env.store, sid, ["INBOX", "Archive"])
    worker = connections.merged(env.config, env.credentials)
    connect.sync(worker, sid)
    monkeypatch.setattr(auth, "_setup_codes", {})
    monkeypatch.setattr(auth, "FAIL_DELAY_SECONDS", 0)
    configure(env.store)
    auth.create_account(env.store, "owner", OWNER)
    with override_settings(TOWPATH_STORE_DIR=env.store, TOWPATH_CONFIG=env.config, ALLOWED_HOSTS=["testserver"],
                           TOWPATH_CREDENTIALS_DIR=env.credentials):
        client = Client()
        assert client.post("/login", {"username": "owner", "password": OWNER}).status_code == 302
        yield env, client, sid, worker
    env.server.__exit__(None, None, None)


def item_id(env, subject: str) -> str:
    with closing(open_store(env.store, "mail", "web")) as db:
        return db.execute("SELECT item_id FROM messages WHERE subject = ?", (subject,)).fetchone()[0]


def test_the_owner_manages_reads_and_asks_for_changes_that_stay_dry_runs(mail):
    env, client, sid, worker = mail
    page = client.get("/mail/").content.decode()
    assert "Manage this account" in page
    client.post("/mail/manage", {"source_id": sid, "manage": "1"})
    analysis.analyze_pending(worker)
    page = client.get("/mail/").content.decode()
    assert "Read 8 of 8 indexed messages, the whole history" in page
    assert "every change is a dry run" in page and "Add a write password" in page
    listing = client.get(f"/mail/{sid}/").content.decode()
    assert "Lock report 7" in listing
    message = item_id(env, "Lock report 3")
    reader = client.get(f"/mail/m/{message}/").content.decode()
    assert "Count 3 of the narrowboats." in reader and "Mark as read" in reader
    assert client.post("/mail/act", {"item_id": message, "type": "delete"}).status_code == 400
    assert client.post("/mail/act", {"item_id": message, "type": "archive"}).status_code == 302
    executor.run_pending(worker, env.credentials, pause=0)
    page = client.get(f"/mail/m/{message}/").content.decode()
    assert "Dry run" in page and "would archive" in page
    assert env.state.writes == [] and env.state.violations == []
    assert len(env.state.mailboxes["INBOX"].messages) == 7


def test_a_read_only_password_is_refused_and_a_write_password_is_tested_and_kept(mail):
    env, client, sid, worker = mail
    with override_settings(ROOT_URLCONF="towpath.web.connection_urls"):
        setup = Client()
        setup.post("/login", {"username": "owner", "password": OWNER})
        page = setup.get(f"/connections/{sid}/").content.decode()
        assert "Add a write password" in page
        refused = setup.post(f"/connections/{sid}/write-password", {"password": PASSWORD})
        assert refused.status_code == 400 and "That password can only read mail" in refused.content.decode()
        assert not executor.password_path(env.credentials, sid).exists()
        saved = setup.post(f"/connections/{sid}/write-password", {"password": WRITE_PASSWORD})
        assert saved.status_code == 302
        assert executor.password_path(env.credentials, sid).read_text() == WRITE_PASSWORD
        page = setup.get(f"/connections/{sid}/?saved=write").content.decode()
        assert "Write password added" in page and WRITE_PASSWORD not in page
    c = connections.get(env.store, sid, role="web")
    assert "MOVE" in c["settings"]["write"]["capabilities"]
    assert c["settings"]["write"]["mailboxes"]["Archive"] == "Archive"
    assert env.state.writes == []  # testing opened the inbox for writing and changed nothing


def test_turned_on_changes_happen_and_can_be_undone(mail):
    env, client, sid, worker = mail
    client.post("/mail/manage", {"source_id": sid, "manage": "1"})
    analysis.analyze_pending(worker)
    connections.save_write_password(env.store, env.credentials, sid, WRITE_PASSWORD,
                                    connections.test_write_imap(env.settings, WRITE_PASSWORD))
    assert client.post("/mail/writes", {"source_id": sid, "types": ["archive", "move", "bogus"]}).status_code == 302
    assert "Dry runs only." in client.get("/mail/").content.decode()
    client.post("/mail/dry-run", {"source_id": sid, "dry_run": "0"})
    assert "Live." in client.get("/mail/").content.decode()
    message = item_id(env, "Lock report 5")
    client.post("/mail/act", {"item_id": message, "type": "archive"})
    client.post("/mail/act", {"item_id": item_id(env, "Lock report 4"), "type": "mark_read"})  # off: a dry run
    executor.run_pending(worker, env.credentials, pause=0)
    assert len(env.state.mailboxes["INBOX"].messages) == 6 and len(env.state.mailboxes["Archive"].messages) == 2
    page = client.get("/mail/").content.decode()
    assert "Done" in page and "Dry run" in page and "Undo" in page
    done = next(r for r in executor.receipts(env.store) if r["outcome"] == "done")
    assert client.post("/mail/undo", {"key": done["key"]}).status_code == 302
    assert client.post("/mail/undo", {"key": done["key"]}).status_code == 400  # one Undo per change
    executor.run_pending(worker, env.credentials, pause=0)
    assert len(env.state.mailboxes["INBOX"].messages) == 7 and len(env.state.mailboxes["Archive"].messages) == 1
    assert not any(w.startswith(("EXPUNGE", "CLOSE", "DELETE")) for w in env.state.writes)
    assert all(w.split(" ")[0] in ("STORE", "MOVE", "APPEND") for w in env.state.writes)


def test_stopping_management_stops_reading_and_acting(mail):
    env, client, sid, worker = mail
    client.post("/mail/manage", {"source_id": sid, "manage": "1"})
    client.post("/mail/manage", {"source_id": sid, "manage": "0"})
    assert analysis.analyze_pending(worker) == []
    assert client.get(f"/mail/{sid}/").status_code == 302


def test_a_forms_return_address_never_leads_off_towpath():
    from django.test import RequestFactory

    from towpath.web.auth import local_next

    def where(nxt):
        return local_next(RequestFactory().post("/x", {"next": nxt}), "/fallback")

    assert where("/mail/fastmail/settings") == "/mail/fastmail/settings" and where("?state=done") == "/fallback"
    for bad in ("https://elsewhere.example/", "//elsewhere.example/", "/\\elsewhere.example", "javascript:x", ""):
        assert where(bad) == "/fallback"
