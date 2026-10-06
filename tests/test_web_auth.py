"""Single-account login: first-run setup, sign-in, sessions, and proxy addressing. Invented data only."""

from contextlib import closing
import stat

from django.test import Client, override_settings
from django.urls import get_resolver
import pytest
from typer.testing import CliRunner

from towpath.cli import app
from towpath.stores import open_store
from towpath.web import auth
from towpath.web.application import address_settings, configure

PASSWORD = "correct horse battery staple"


@pytest.fixture
def store(tmp_path, monkeypatch):
    with closing(open_store(tmp_path, "source", "connect")) as db:
        db.execute("INSERT INTO sources VALUES ('demo','mail-provider','fixture','{}')")
        db.commit()
    monkeypatch.setattr(auth, "_setup_codes", {})
    monkeypatch.setattr(auth, "_failures", [])
    monkeypatch.setattr(auth, "FAIL_DELAY_SECONDS", 0)
    configure(tmp_path)
    with override_settings(TOWPATH_STORE_DIR=tmp_path, ALLOWED_HOSTS=["testserver"]):
        yield tmp_path


def set_up(client, store, **overrides):
    form = {"code": auth.setup_code(store), "username": "owner", "password": PASSWORD, "confirm": PASSWORD}
    return client.post("/setup", {**form, **overrides})


def protected_paths():
    for pattern in get_resolver().url_patterns:
        route = str(pattern.pattern).replace("<str:item_id>", "x").replace("<str:cid>", "x")
        if "/" + route not in auth.OPEN_PATHS:
            yield "/" + route


def test_every_page_leads_to_setup_until_the_account_exists(store, capsys):
    client = Client()
    paths = list(protected_paths())
    assert {"/", "/email/", "/search/", "/collections/", "/logout"} <= set(paths)
    for path in paths:
        for method in (client.get, client.post):
            response = method(path)
            assert response.status_code == 302 and response["Location"] == "/setup", path
    assert client.get("/setup").status_code == 200
    assert client.get("/assets/app.css").status_code == 200
    assert f"setup code: {auth.setup_code(store)}" in capsys.readouterr().out


def test_setup_needs_the_logged_code_and_a_sound_password(store):
    client = Client()
    assert set_up(client, store, code="000000-000000-000000").status_code == 400
    assert set_up(client, store, password="short", confirm="short").status_code == 400
    assert set_up(client, store, confirm=PASSWORD + "!").status_code == 400
    assert set_up(client, store, username="").status_code == 400
    assert auth.account(store) is None
    response = set_up(client, store)
    assert response.status_code == 302 and response["Location"] == "/"
    assert client.get("/").status_code == 200
    # The account file holds a salted hash only, readable by the service alone.
    raw = (store / auth.LOGIN_FILE).read_text()
    assert PASSWORD not in raw and "pbkdf2_sha256$" in raw
    assert stat.S_IMODE((store / auth.LOGIN_FILE).stat().st_mode) == 0o600
    # Setup cannot run twice, even with the old code.
    other = Client()
    assert other.get("/setup")["Location"] == "/login"
    assert set_up(other, store, code="anything", username="intruder")["Location"] == "/login"
    assert auth.account(store)["username"] == "owner"


def test_sign_in_sign_out_and_redirect_targets(store):
    set_up(Client(), store)
    client = Client()
    response = client.get("/collections/?x=1")
    assert response.status_code == 302 and response["Location"] == "/login?next=%2Fcollections%2F%3Fx%3D1"
    assert client.post("/login", {"username": "owner", "password": "wrong"}).status_code == 401
    assert client.post("/login", {"username": "other", "password": PASSWORD}).status_code == 401
    response = client.post("/login", {"username": "owner", "password": PASSWORD, "next": "/collections/?x=1"})
    assert response["Location"] == "/collections/?x=1"
    assert client.get("/collections/").status_code == 200
    for target in ("//evil.example", "https://evil.example/", "javascript:alert(1)"):
        fresh = Client()
        response = fresh.post("/login", {"username": "owner", "password": PASSWORD, "next": target})
        assert response["Location"] == "/", target
    assert client.post("/logout")["Location"] == "/login"
    assert client.get("/")["Location"].startswith("/login")


def test_forms_still_need_csrf_tokens(store):
    set_up(Client(), store)
    strict = Client(enforce_csrf_checks=True)
    assert strict.post("/login", {"username": "owner", "password": PASSWORD}).status_code == 403
    page = strict.get("/login")
    token = page.cookies["csrftoken"].value
    response = strict.post("/login", {"username": "owner", "password": PASSWORD, "csrfmiddlewaretoken": token})
    assert response.status_code == 302
    assert strict.post("/logout").status_code == 403


def test_reset_and_forged_cookies_sign_everyone_out(store):
    client = Client()
    set_up(client, store)
    assert client.get("/").status_code == 200
    forged = Client()
    forged.cookies[auth.COOKIE] = client.cookies[auth.COOKIE].value[:-2] + "xx"
    assert forged.get("/").status_code == 302
    result = CliRunner().invoke(app, ["web", "reset-login", "--store-dir", str(store), "--yes"])
    assert result.exit_code == 0 and auth.account(store) is None
    assert client.get("/")["Location"] == "/setup"
    # A new account with the same name does not revive old sessions.
    old_cookie = client.cookies[auth.COOKIE].value
    set_up(Client(), store)
    stale = Client()
    stale.cookies[auth.COOKIE] = old_cookie
    assert stale.get("/").status_code == 302


def test_repeated_failures_pause_sign_in(store):
    set_up(Client(), store)
    client = Client()
    for _ in range(auth.FAIL_LIMIT):
        assert client.post("/login", {"username": "owner", "password": "wrong"}).status_code == 401
    assert client.post("/login", {"username": "owner", "password": PASSWORD}).status_code == 429


def test_signing_key_persists_across_restarts(store):
    key = auth.secret_key(store)
    assert auth.secret_key(store) == key and len(key) == 64
    assert stat.S_IMODE((store / auth.SECRET_FILE).stat().st_mode) == 0o600


def test_public_url_behind_a_tls_proxy(store):
    proxied = address_settings("https://towpath.example.org")
    assert proxied["ALLOWED_HOSTS"] == ["127.0.0.1", "localhost", "towpath.example.org"]
    assert proxied["CSRF_TRUSTED_ORIGINS"] == ["https://towpath.example.org"]
    assert address_settings(None) == {"ALLOWED_HOSTS": ["127.0.0.1", "localhost"]}
    for bad in ("towpath.example.org", "ftp://towpath.example.org", "https://towpath.example.org/sub"):
        with pytest.raises(ValueError):
            address_settings(bad)
    with override_settings(**proxied):
        client = Client(enforce_csrf_checks=True, HTTP_HOST="towpath.example.org", HTTP_X_FORWARDED_PROTO="https")
        assert Client(HTTP_HOST="other.example.org").get("/setup").status_code == 400
        token = client.get("/setup", secure=True).cookies["csrftoken"].value
        response = client.post("/setup", {"code": auth.setup_code(store), "username": "owner", "password": PASSWORD,
                                          "confirm": PASSWORD, "csrfmiddlewaretoken": token},
                               HTTP_ORIGIN="https://towpath.example.org", HTTP_REFERER="https://towpath.example.org/setup")
        assert response.status_code == 302
        session = response.cookies[auth.COOKIE]
        assert session["secure"] and session["httponly"] and session["samesite"] == "Lax"


def test_serve_rejects_a_malformed_public_url(store):
    result = CliRunner().invoke(app, ["web", "serve", "--store-dir", str(store), "--public-url", "towpath.example.org"])
    assert result.exit_code == 2
