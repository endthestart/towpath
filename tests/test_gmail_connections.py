"""Gmail on the Connections page: importing the configured connection, Google sign-in, reconnecting and pacing.

Google is never contacted: the consent URL is built locally by the real library, and the token exchange,
scope check and profile call are replaced by fakes. Invented data only.
"""

from contextlib import closing
import json
import stat
from urllib.parse import parse_qs, urlsplit

from django.test import Client, override_settings
import pytest

from towpath import config as config_mod, connections, google_oauth
from towpath.stores import open_store
from towpath.web import auth
from towpath.web.application import configure

OWNER = "correct horse battery staple"
TOKEN = json.dumps({"token": "access", "refresh_token": "refresh", "client_id": "c", "client_secret": "s",
                    "token_uri": "https://oauth2.googleapis.com/token", "scopes": [google_oauth.READONLY_SCOPE]})
CLIENT_ID = "123-abc.apps.googleusercontent.com"


@pytest.fixture
def instance(tmp_path):
    creds = tmp_path / "credentials"
    creds.mkdir()
    (creds / "gmail-token.json").write_text(TOKEN)
    (tmp_path / "towpath.toml").write_text(f"""
[stores]
dir = "state"

[[sources]]
id = "mail"
kind = "mail-provider"
adapter = "gmail"
client_secrets = "client.json"
token = "{creds / 'gmail-token.json'}"

[gmail_pacing]
budget_id = "towpath-readonly"
units_per_minute = 1800
min_interval_seconds = 0.667
verified_units_per_minute = 6000
""")
    config = config_mod.load(tmp_path / "towpath.toml")
    with closing(open_store(config.store_dir, "source", "connect")) as db:
        db.execute("INSERT INTO sources VALUES ('mail','mail-provider','gmail','{}')")
        db.execute("""INSERT INTO runs(run_id,source_id,kind,started_at,finished_at,complete,termination)
                      VALUES ('r1','mail','full','2026-05-01T00:00:00+00:00','2026-05-02T00:00:00+00:00',1,
                      'complete')""")
        for n in range(5):
            db.execute("""INSERT INTO items(item_id,source_id,native_id,subject,from_addr,internal_date,first_seen_run,
                          last_seen_run) VALUES (?, 'mail', ?, 'Lock report', 'a@example.com', '0', 'r1', 'r1')""",
                       (f"i{n}", str(n)))
        db.commit()
    return config, creds


def test_the_configured_gmail_connection_is_imported_once_without_reconsent(instance):
    config, creds = instance
    assert connections.import_configured(config, creds) == ["mail"]
    assert connections.import_configured(config, creds) == []
    c = connections.get(config.store_dir, "mail")
    assert (c["provider"], c["state"], c["indexing"], c["progress"]["indexed"]) == ("gmail", "ready", "idle", 5)
    assert c["settings"] == {"token": "gmail-token.json", "origin": "config", "username": None}
    merged = connections.merged(config, creds)
    assert merged.sources["mail"].token == creds / "gmail-token.json"  # the same token file, refreshed in place
    assert connections.merged(config, None, role="web").sources["mail"].token is None
    assert merged.gmail_pacing == config.gmail_pacing


def test_pacing_follows_the_verified_quota_and_refuses_unsafe_values(instance):
    config, creds = instance
    connections.set_verified_quota(config, 2000)
    pacing = connections.merged(config, creds).gmail_pacing
    assert (pacing.verified_units_per_minute, pacing.units_per_minute) == (2000, 600)
    assert connections.gmail_pacing(config, role="web").units_per_minute == 600
    connections.set_verified_quota(config, 100_000)
    assert connections.gmail_pacing(config).units_per_minute == 1800  # never above the ceiling
    for bad in (0, -5):
        with pytest.raises(connections.ConnectionProblem):
            connections.set_verified_quota(config, bad)
    connections.set_verified_quota(config, None)
    pacing = connections.gmail_pacing(config)
    assert pacing.verified_units_per_minute is None and pacing.units_per_minute == 1200


def test_the_consent_url_asks_for_read_only_mail_with_pkce(instance):
    _, creds = instance
    connections.save_google_client(creds, CLIENT_ID, "client-secret", "https://towpath.example.org/cb")
    assert stat.S_IMODE((creds / connections.GOOGLE_CLIENT_FILE).stat().st_mode) == 0o600
    url, state, verifier = google_oauth.consent_url(creds, "https://towpath.example.org/connections/google/callback")
    query = parse_qs(urlsplit(url).query)
    assert query["scope"] == [google_oauth.READONLY_SCOPE] and query["access_type"] == ["offline"]
    assert query["redirect_uri"] == ["https://towpath.example.org/connections/google/callback"]
    assert query["code_challenge_method"] == ["S256"] and query["state"] == [state] and len(verifier) >= 43
    assert "client-secret" not in url
    with pytest.raises(connections.ConnectionProblem):
        connections.save_google_client(creds, "not-a-client-id", "s", "https://x/cb")


class FakeFlow:
    def __init__(self, refresh_token="refresh"):
        self.credentials = type("Creds", (), {"token": "access", "refresh_token": refresh_token,
                                              "to_json": lambda self: TOKEN})()

    def fetch_token(self, code):
        assert code == "the-code"


def test_finishing_refuses_broader_scopes_and_short_lived_grants(instance, monkeypatch):
    _, creds = instance
    monkeypatch.setattr(google_oauth, "_flow", lambda *a, **k: FakeFlow())
    monkeypatch.setattr(google_oauth, "tokeninfo_scopes", lambda token: {google_oauth.READONLY_SCOPE})
    assert google_oauth.finish(creds, "https://x/cb", "the-code", "v") == TOKEN
    monkeypatch.setattr(google_oauth, "tokeninfo_scopes",
                        lambda token: {google_oauth.READONLY_SCOPE, "https://www.googleapis.com/auth/gmail.modify"})
    with pytest.raises(connections.ConnectionProblem, match="Refused"):
        google_oauth.finish(creds, "https://x/cb", "the-code", "v")
    monkeypatch.setattr(google_oauth, "_flow", lambda *a, **k: FakeFlow(refresh_token=None))
    with pytest.raises(connections.ConnectionProblem, match="lasting sign-in"):
        google_oauth.finish(creds, "https://x/cb", "the-code", "v")


@pytest.fixture
def pages(instance, monkeypatch):
    config, creds = instance
    monkeypatch.setattr(auth, "FAIL_DELAY_SECONDS", 0)
    configure(config.store_dir)
    auth.create_account(config.store_dir, "owner", OWNER)
    signed_in = {"addresses": ["owner@example.com"]}
    monkeypatch.setattr(google_oauth, "consent_url", lambda c, redirect: (
        f"https://accounts.example.com/auth?redirect={redirect}", "st4te", "verif"))
    monkeypatch.setattr(google_oauth, "finish", lambda c, redirect, code, verifier: TOKEN)
    monkeypatch.setattr(google_oauth, "address", lambda config, sid, path: signed_in["addresses"].pop(0))
    with override_settings(TOWPATH_STORE_DIR=config.store_dir, TOWPATH_CONFIG=config, ALLOWED_HOSTS=["testserver"],
                           ROOT_URLCONF="towpath.web.connection_urls", TOWPATH_CREDENTIALS_DIR=creds,
                           TOWPATH_PUBLIC_URL=None):
        client = Client()
        client.post("/login", {"username": "owner", "password": OWNER})
        yield config, creds, client, signed_in


def test_listing_imports_the_configured_gmail_and_shows_its_pacing(pages):
    config, _, client, _ = pages
    html = client.get("/connections/").content.decode()
    assert "5 messages indexed" in html and "Set up from the configuration file" not in html
    detail = client.get("/connections/mail/").content.decode()
    assert "All mail" in detail and "30% of your project's verified 6,000" in detail and "Reconnect Gmail" in detail
    assert client.post("/connections/mail/pacing", {"verified": "abc"}).status_code == 400
    assert client.post("/connections/mail/pacing", {"verified": "3,000"}).status_code == 302
    assert connections.gmail_pacing(config).units_per_minute == 900


def test_google_client_setup_then_sign_in_adds_a_gmail_connection(pages):
    config, creds, client, _ = pages
    guide = client.get("/connections/add/gmail").content.decode()
    assert "One-time setup in Google Cloud" in guide and "http://testserver/connections/google/callback" in guide
    assert client.post("/connections/google/client", {"client_id": "x", "client_secret": "y"}).status_code == 400
    assert client.post("/connections/google/client",
                       {"client_id": CLIENT_ID, "client_secret": "client-secret"}).status_code == 302
    page = client.get("/connections/add/gmail").content.decode()
    assert "Connect Gmail" in page and "client-secret" not in page
    start = client.post("/connections/google/start")
    assert start.status_code == 302 and start["Location"].startswith("https://accounts.example.com/auth")
    assert client.get("/connections/google/callback", {"state": "forged", "code": "c"}).status_code == 400
    assert client.get("/connections/google/callback", {"error": "access_denied", "state": "st4te"}).status_code == 400
    done = client.get("/connections/google/callback", {"state": "st4te", "code": "the-code"})
    assert done["Location"] == "/connections/gmail/"
    c = connections.get(config.store_dir, "gmail")
    assert c["settings"]["username"] == "owner@example.com" and c["state"] == "ready"
    path = connections.token_path(creds, c)
    assert path.read_text() == TOKEN and stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not list(creds.glob(".pending-*"))
    connections.start_indexing(config.store_dir, creds, "gmail")  # a token is present, so indexing may start


def test_a_callback_without_the_signed_cookie_is_refused(pages):
    _, _, client, _ = pages
    assert client.get("/connections/google/callback", {"state": "st4te", "code": "c"}).status_code == 400


def test_reconnecting_must_use_the_same_google_account(pages):
    config, creds, client, signed_in = pages
    client.get("/connections/")  # imports "mail"
    connections.save_google_client(creds, CLIENT_ID, "client-secret", "http://testserver/connections/google/callback")
    with closing(open_store(config.store_dir, "connections", "connect")) as db:
        db.execute("""UPDATE connections SET settings = json_set(settings, '$.username', 'owner@example.com')
                      WHERE source_id = 'mail'""")
        db.commit()
    original = (creds / "gmail-token.json").read_text()
    signed_in["addresses"] = ["someone-else@example.com", "owner@example.com"]
    client.post("/connections/google/start", {"reconnect": "mail"})
    refused = client.get("/connections/google/callback", {"state": "st4te", "code": "c"})
    assert refused.status_code == 400 and "same Google account" in refused.content.decode()
    assert (creds / "gmail-token.json").read_text() == original
    client.post("/connections/google/start", {"reconnect": "mail"})
    done = client.get("/connections/google/callback", {"state": "st4te", "code": "c"})
    assert done["Location"] == "/connections/mail/"
    assert connections.get(config.store_dir, "mail")["last_error"] is None


def test_disconnecting_gmail_deletes_its_token_and_keeps_the_index(pages):
    config, creds, client, _ = pages
    client.get("/connections/")
    assert "third-party connections" in client.get("/connections/mail/disconnect").content.decode()
    client.post("/connections/mail/disconnect")
    assert not (creds / "gmail-token.json").exists()
    with pytest.raises(connections.ConnectionProblem, match="Reconnect"):
        connections.start_indexing(config.store_dir, creds, "mail")
    with closing(open_store(config.store_dir, "source", "web")) as db:
        assert db.execute("SELECT COUNT(*) FROM items WHERE source_id = 'mail'").fetchone()[0] == 5
