"""Stored provider-search results shown by the UI obey current grants, exclusions, scope and availability."""

import re
import sys
from pathlib import Path
from urllib.parse import unquote

import pytest
from django.test import Client, override_settings

from towpath import config as config_mod
from towpath import connect
from towpath.discovery import policy
from towpath.unified import requests
from towpath.unified.contracts import Filters
from towpath.web.application import configure

sys.path.insert(0, str(Path(__file__).parent))
from unified_env import Uni  # noqa: E402

MIDDLEWARE = ["towpath.web.views.LocalPrivacyMiddleware", "towpath.web.views.StoreSchemaMiddleware",
              "django.middleware.csrf.CsrfViewMiddleware"]


@pytest.fixture
def uni(tmp_path, monkeypatch):
    instance = Uni(tmp_path, monkeypatch)
    try:
        yield instance
    finally:
        instance.close()


def store_search(uni, query: str) -> dict:
    """Queue and run one provider search as towpath-connect; return the stored refs by source."""
    requests.request_search(uni.config.store_dir, Filters.parse(query))
    requests.run_searches(uni.config)
    stored = requests.latest_search(uni.config.store_dir, Filters.parse(query))["response"]
    out: dict = {}
    for r in stored["results"]:
        out.setdefault(r["source_id"], set()).add(r["ref"])
        out.setdefault("root:" + str(r["locator"].get("root")), set()).add(r["ref"])
    return out


def ui_provider_refs(uni, query: str, config, monkeypatch) -> tuple[set, str]:
    """The reference links in the UI's stored-provider section, with no path to any provider."""
    def forbidden(*a, **k):
        pytest.fail("the UI attempted to contact a source")

    monkeypatch.setattr("towpath.adapters.build_connector", forbidden)
    monkeypatch.setattr("towpath.discovery.service.provider", forbidden)
    uni.state.transcript.clear()
    configure(uni.config.store_dir, config)
    with override_settings(TOWPATH_LOGIN_REQUIRED=False, TOWPATH_STORE_DIR=uni.config.store_dir, TOWPATH_CONFIG=config,
                           ALLOWED_HOSTS=["testserver"], MIDDLEWARE=MIDDLEWARE):
        html = Client().get("/search/", {"q": query}).content.decode()
    monkeypatch.undo()
    assert uni.state.transcript == []
    section = html.split('id="full-text"', 1)[1].split('class="index-results"', 1)[0]
    refs = {unquote(r) for r in re.findall(r'href="/ref/\?r=([^"]+)"', section)}
    return refs, section


def reload(uni, old: str, new: str):
    uni.path.write_text(uni.path.read_text().replace(old, new))
    return config_mod.load(uni.path)


def test_revoked_root_grant_withholds_its_stored_results_and_keeps_the_rest(uni, monkeypatch):
    stored = store_search(uni, "canal source:files-fixture")
    archive = stored["root:archive"]
    assert len(archive) == 3 and len(stored["root:shared"]) == 1
    policy.revoke(uni.config, "archive", "search")
    shown, section = ui_provider_refs(uni, "canal source:files-fixture", uni.config, monkeypatch)
    assert shown.isdisjoint(archive)
    assert shown == stored["files-fixture"] - archive  # the shared root's result is still permitted
    assert "3 stored results withheld" in section and "no search grant" in section


def test_a_new_exclusion_withholds_matching_stored_results(uni, monkeypatch):
    stored = store_search(uni, "canal source:files-fixture")
    config = reload(uni, 'path = "files/roots/shared"', 'path = "files/roots/shared"\nexclude = ["copy-of-*"]')
    shown, section = ui_provider_refs(uni, "canal source:files-fixture", config, monkeypatch)
    assert shown == stored["files-fixture"] - stored["root:shared"]
    assert "excluded" in section


def test_removed_or_disabled_sources_withhold_everything_they_stored(uni, monkeypatch):
    stored = store_search(uni, "canal")
    assert {"gmail-fixture", "imap-fixture", "files-fixture"} <= set(stored)
    text = uni.path.read_text()
    imap_block = text[text.index("[[sources]]\nid = \"imap-fixture\""):text.index("[files]")]
    uni.path.write_text(text.replace(imap_block, "").replace("[files]\n", "[files]\nenabled = false\n"))
    config = config_mod.load(uni.path)
    shown, section = ui_provider_refs(uni, "canal", config, monkeypatch)
    assert shown == stored["gmail-fixture"]
    assert "no longer configured" in section and "file discovery is disabled" in section


def test_stored_mail_results_no_longer_in_the_source_or_scope_are_withheld(uni, monkeypatch):
    stored = store_search(uni, "canal source:imap-fixture")
    assert len(stored["imap-fixture"]) == 2
    del uni.state.mailboxes["INBOX"].messages[2]
    assert connect.sync(uni.config, "imap-fixture")["absent"] == 1
    shown, section = ui_provider_refs(uni, "canal source:imap-fixture", uni.config, monkeypatch)
    assert shown == {r for r in stored["imap-fixture"] if r.startswith("imap-fixture:Archive")}
    assert "no longer in the source" in section
    scoped = reload(uni, 'timeout_seconds = 10', 'timeout_seconds = 10\nmailboxes = ["INBOX"]')
    shown, section = ui_provider_refs(uni, "canal source:imap-fixture", scoped, monkeypatch)
    assert shown == set() and "outside the configured mailboxes" in section


def test_permitted_stored_results_are_shown_unchanged(uni, monkeypatch):
    stored = store_search(uni, "canal")
    shown, section = ui_provider_refs(uni, "canal", uni.config, monkeypatch)
    everything = set().union(*(v for k, v in stored.items() if not k.startswith("root:")))
    assert shown == everything and "withheld" not in section


def test_without_a_configuration_file_results_cannot_be_checked_and_are_withheld(uni, monkeypatch):
    stored = store_search(uni, "canal")
    shown, section = ui_provider_refs(uni, "canal", None, monkeypatch)
    assert shown == stored["gmail-fixture"] | stored["imap-fixture"]
    assert "start the UI with --config" in section
