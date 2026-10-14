"""The Life page: the owner chooses which mail the life story may use and whether its text may reach the AI model
connected on the Connections page. Invented accounts and a stand-in model server only."""

import sys
from pathlib import Path

import pytest
from django.test import Client, override_settings

from towpath import connections, decisions
from towpath.unified import grants
from towpath.web import auth
from towpath.web.application import configure

sys.path.insert(0, str(Path(__file__).parent))
from test_connections import PASSWORD, Env  # noqa: E402

OWNER = "correct horse battery staple"


@pytest.fixture
def life_page(tmp_path, monkeypatch, openai_stub):
    env = Env(tmp_path)
    monkeypatch.setattr(auth, "_setup_codes", {})
    monkeypatch.setattr(auth, "FAIL_DELAY_SECONDS", 0)
    sid = connections.add_imap(env.store, env.credentials, "imap", env.settings, PASSWORD,
                               connections.test_imap(env.settings, PASSWORD))
    connections.choose_folders(env.store, sid, ["INBOX"])
    stub = openai_stub(model="local-9b")
    form = {"base_url": f"http://127.0.0.1:{stub.port}/v1", "model": "local-9b"}
    model = connections.add_model(env.store, env.credentials, form, "token-1", connections.test_model(form, "token-1"))
    configure(env.store)
    auth.create_account(env.store, "owner", OWNER)
    with override_settings(TOWPATH_STORE_DIR=env.store, TOWPATH_CONFIG=env.config, ALLOWED_HOSTS=["testserver"]):
        client = Client(enforce_csrf_checks=False)
        assert client.post("/login", {"username": "owner", "password": OWNER}).status_code == 302
        yield env, client, sid, model
    env.close()


def test_the_owner_allows_an_account_and_the_model_and_can_take_both_back(life_page):
    env, client, sid, model = life_page
    page = client.get("/life/").content.decode()
    assert "not used" in page and "receives no personal content yet" in page and "None yet" in page
    assert client.post("/life/account", {"source_id": "nobody", "allow": "1"}).status_code == 400
    assert client.post("/life/model", {"endpoint_id": "other", "allow": "1"}).status_code == 400
    client.post("/life/account", {"source_id": sid, "allow": "1"})
    client.post("/life/model", {"endpoint_id": model, "allow": "1"})
    page = client.get("/life/?saved=model").content.decode()
    assert "used for your life story" in page and "may read the text of the mail you chose" in page
    worker = connections.merged(env.config, env.credentials)
    assert "life-evidence" in grants.granted(env.store)[sid]
    assert decisions.model_granted(worker, model, "content")  # the worker sees the owner's grant
    client.post("/life/model", {"endpoint_id": model, "allow": "0"})
    client.post("/life/account", {"source_id": sid, "allow": "0"})
    assert not decisions.model_granted(worker, model, "content") and sid not in grants.granted(env.store)


def test_a_changed_model_needs_allowing_again(life_page):
    env, client, sid, model = life_page
    client.post("/life/model", {"endpoint_id": model, "allow": "1"})
    c = connections.get(env.store, model)
    connections._update(env.store, model, settings={**c["settings"], "model": "local-27b"})
    assert not decisions.model_granted(connections.merged(env.config, env.credentials), model, "content")
    assert "receives no personal content yet" in client.get("/life/").content.decode()
