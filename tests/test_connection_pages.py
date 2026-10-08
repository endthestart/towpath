"""The connector's setup pages, driven like a person would: add, choose folders, index, pause, replace the
password, disconnect. Synthetic IMAP server only; the password must never come back in any page."""

import re
import sys
from pathlib import Path

from django.test import Client, override_settings
import pytest

from towpath import connections, request_worker
from towpath.web import auth
from towpath.web.application import configure

sys.path.insert(0, str(Path(__file__).parent))
from test_connections import PASSWORD, Env  # noqa: E402

OWNER = "correct horse battery staple"


@pytest.fixture
def pages(tmp_path, monkeypatch):
    env = Env(tmp_path)
    monkeypatch.setattr(auth, "_setup_codes", {})
    monkeypatch.setattr(auth, "FAIL_DELAY_SECONDS", 0)
    configure(env.store)
    auth.create_account(env.store, "owner", OWNER)
    with override_settings(TOWPATH_STORE_DIR=env.store, TOWPATH_CONFIG=env.config, ALLOWED_HOSTS=["testserver"],
                           ROOT_URLCONF="towpath.web.connection_urls", TOWPATH_CREDENTIALS_DIR=env.credentials):
        client = Client()
        assert client.post("/login", {"username": "owner", "password": OWNER}).status_code == 302
        yield env, client
    env.close()


def add_form(env, **change):
    return {"username": env.state.username, "password": PASSWORD, "host": "127.0.0.1", "port": env.server.port,
            "security": "plain-loopback", **change}


def test_signed_out_visitors_are_sent_to_sign_in(pages):
    env, _ = pages
    anonymous = Client()
    for path in ("/connections/", "/connections/add/fastmail", "/connections/x/"):
        assert anonymous.get(path)["Location"].startswith("/login")
    assert anonymous.post("/connections/add/imap", add_form(env))["Location"] == "/login"
    assert connections.all_connections(env.store) == []


def test_fastmail_page_explains_the_app_password(pages):
    _, client = pages
    html = client.get("/connections/add/fastmail").content.decode()
    assert "make an app password in Fastmail" in html and "Privacy &amp; Security" in html
    assert 'type="password"' in html and 'name="host"' not in html  # the server is filled in for Fastmail


def test_a_wrong_password_is_explained_and_never_echoed(pages):
    env, client = pages
    response = client.post("/connections/add/imap", add_form(env, password="not-the-password"))
    html = response.content.decode()
    assert response.status_code == 400 and "rejected the address or password" in html
    assert "not-the-password" not in html and env.state.username in html  # the address is kept, the password not
    assert connections.all_connections(env.store) == []


def test_add_choose_folders_index_and_watch_progress(pages):
    env, client = pages
    response = client.post("/connections/add/imap", add_form(env))
    sid = connections.all_connections(env.store)[0]["source_id"]
    assert response["Location"] == f"/connections/{sid}/folders"
    html = client.get(response["Location"]).content.decode()
    ticked = re.findall(r'name="folder" value="([^"]+)" checked', html)
    assert sorted(ticked) == ["Archive", "INBOX"] and "Trash" in html and "7 messages" in html
    response = client.post(f"/connections/{sid}/folders", {"folder": ["INBOX"], "start": "1"})
    assert response["Location"] == f"/connections/{sid}/"
    html = client.get(f"/connections/{sid}/").content.decode()
    assert "Starting to index" in html and ">Pause<" in html
    assert 'hx-get="/connections/%s/live"' % sid in html and "/assets/htmx.min.js" in html  # the panel polls
    panel = client.get(f"/connections/{sid}/live").content.decode()
    assert panel.startswith('<div id="connection-live"') and "<html" not in panel and "Starting to index" in panel
    request_worker.run_once(env.config, env.credentials)
    html = client.get(f"/connections/{sid}/").content.decode()
    assert ">7<" in html and "Check for new mail" in html and "hx-get" not in html and "htmx" not in html
    listing = client.get("/connections/").content.decode()
    assert "hx-get" not in listing  # nothing busy, nothing polls
    assert "7 messages indexed" in listing and "Indexed" in listing
    for page in (html, listing):
        assert PASSWORD not in page
    env.assert_read_only()


def test_pause_resume_replace_and_disconnect(pages):
    env, client = pages
    client.post("/connections/add/imap", add_form(env))
    sid = connections.all_connections(env.store)[0]["source_id"]
    client.post(f"/connections/{sid}/folders", {"folder": ["INBOX"], "start": "1"})
    client.post(f"/connections/{sid}/pause")
    assert "Resume indexing" in client.get(f"/connections/{sid}/").content.decode()
    assert request_worker.run_once(env.config, env.credentials)["indexing"] == []
    env.state.password = "rotated-app-password"
    response = client.post(f"/connections/{sid}/password", {"password": "wrong"})
    assert response.status_code == 400 and "rotated-app-password" not in response.content.decode()
    assert client.post(f"/connections/{sid}/password", {"password": "rotated-app-password"}).status_code == 302
    assert connections.secret_path(env.credentials, sid).read_text() == "rotated-app-password"
    client.post(f"/connections/{sid}/start")
    request_worker.run_once(env.config, env.credentials)
    assert connections.get(env.store, sid)["progress"]["indexed"] == 7
    assert "Disconnect" in client.get(f"/connections/{sid}/disconnect").content.decode()
    client.post(f"/connections/{sid}/disconnect")
    assert not connections.secret_path(env.credentials, sid).exists()
    assert "Reconnect with a password" in client.get(f"/connections/{sid}/").content.decode()


def test_setup_forms_need_csrf_tokens(pages):
    env, _ = pages
    strict = Client(enforce_csrf_checks=True)
    page = strict.get("/login")
    token = page.cookies["csrftoken"].value
    strict.post("/login", {"username": "owner", "password": OWNER, "csrfmiddlewaretoken": token})
    assert strict.post("/connections/add/imap", add_form(env)).status_code == 403
    assert connections.all_connections(env.store) == []


def test_only_fastmail_passwords_lose_their_spaces(pages):
    env, client = pages
    env.state.password = "pass with spaces"
    assert client.post("/connections/add/imap", add_form(env, password="pass with spaces")).status_code == 302
    sid = connections.all_connections(env.store)[0]["source_id"]
    assert connections.secret_path(env.credentials, sid).read_text() == "pass with spaces"
