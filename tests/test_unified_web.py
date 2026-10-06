"""Unified search UI, reference inspection, queued requests and durable collections (web role, local mode)."""

import hashlib
import json
import re
import sys
from contextlib import closing, contextmanager
from pathlib import Path
from urllib.parse import unquote

import pytest
from django.test import Client, override_settings

from towpath import connect
from towpath.discovery import service
from towpath.stores import open_store
from towpath.unified import collections, federation, requests, sources
from towpath.unified.contracts import Filters
from towpath.web.application import configure

sys.path.insert(0, str(Path(__file__).parent))
from unified_env import Uni  # noqa: E402

MIDDLEWARE = ["towpath.web.views.LocalPrivacyMiddleware", "django.middleware.csrf.CsrfViewMiddleware"]


@pytest.fixture
def uni(tmp_path, monkeypatch):
    u = Uni(tmp_path, monkeypatch)
    yield u
    u.close()


UI_ONLY = {"active": False}


@contextmanager
def connect_role(uni):
    """A towpath-connect step inside a UI test (an import, a fetch, a queued search run)."""
    UI_ONLY["active"] = False
    try:
        yield
    finally:
        UI_ONLY["active"] = True
        uni.state.transcript.clear()


@pytest.fixture
def web(uni, monkeypatch):
    """A client of the UI in local mode, with every path to a provider made to fail loudly."""
    import towpath.adapters
    import towpath.discovery.service as files_service

    def guarded(real):
        def call(*args, **kwargs):
            if UI_ONLY["active"]:
                pytest.fail("the UI attempted to contact a source")
            return real(*args, **kwargs)
        return call

    configure(uni.config.store_dir, uni.config)
    with override_settings(TOWPATH_STORE_DIR=uni.config.store_dir, TOWPATH_CONFIG=uni.config,
                           ALLOWED_HOSTS=["testserver"], MIDDLEWARE=MIDDLEWARE):
        monkeypatch.setattr(towpath.adapters, "build_connector", guarded(towpath.adapters.build_connector))
        monkeypatch.setattr(files_service, "provider", guarded(files_service.provider))
        uni.state.transcript.clear()
        UI_ONLY["active"] = True
        try:
            yield Client()
        finally:
            UI_ONLY["active"] = False
        assert uni.state.transcript == []  # the synthetic IMAP server never heard from the UI


def _refs(html: str) -> list[str]:
    return re.findall(r'href="/ref/\?r=([^"]+)"', html)


def test_metadata_query_shows_each_source_with_depth_status_and_honest_completeness(web):
    page = web.get("/search/", {"q": "extension:nef"})
    assert page.status_code == 200
    html = page.content.decode()
    for source in ("gmail-fixture", "imap-fixture", "files-fixture", "files-inventory"):
        assert source in html
    assert "Not a complete answer." in html and "inventory of the declared scope is not known" in html
    assert html.count("catalog") >= 4 and "no combined total" in html
    assert len(_refs(html)) == 5  # 1 Gmail part, 1 IMAP part, 3 inventory references
    assert "DCIM/DSC_0001.NEF" in html


def test_keyword_query_is_catalog_only_and_offers_a_queued_provider_search(web, uni):
    html = web.get("/search/", {"q": "fundraiser"}).content.decode()
    assert "keyword text matched metadata only" in html
    assert "Queue provider search" in html and "Canal boat club minutes" not in html
    response = web.post("/search/request", {"q": "fundraiser"})
    assert response.status_code == 302
    assert "Waiting for towpath-connect" in web.get("/search/", {"q": "fundraiser"}).content.decode()
    with closing(open_store(uni.config.store_dir, "queue", "connect")) as q:
        assert [json.loads(r["filters"])["text"] for r in q.execute("SELECT filters FROM search_requests")] == [
            "fundraiser"]


def test_queued_provider_results_are_shown_as_stored_and_dated(uni):
    requests.request_search(uni.config.store_dir, Filters.parse("fundraiser"))
    assert requests.run_searches(uni.config) == {"requests": 1, "searches_run": 1}
    configure(uni.config.store_dir, uni.config)
    with override_settings(TOWPATH_STORE_DIR=uni.config.store_dir, TOWPATH_CONFIG=uni.config,
                           ALLOWED_HOSTS=["testserver"], MIDDLEWARE=MIDDLEWARE):
        html = Client().get("/search/", {"q": "fundraiser"}).content.decode()
    assert "Last provider search ran at" in html and "stored result, not live" in html
    assert "Canal boat club minutes" in html and "not a verified passage" in html


def test_posts_need_a_csrf_token(uni):
    configure(uni.config.store_dir, uni.config)
    with override_settings(TOWPATH_STORE_DIR=uni.config.store_dir, TOWPATH_CONFIG=uni.config,
                           ALLOWED_HOSTS=["testserver"], MIDDLEWARE=MIDDLEWARE):
        strict = Client(enforce_csrf_checks=True)
        for path, data in (("/search/request", {"q": "canal"}), ("/collections/new", {"name": "x"}),
                           ("/ref/request", {"r": "gmail-fixture:x#part=1"})):
            assert strict.post(path, data).status_code == 403
        assert collections.list_all(uni.config.store_dir) == []
        page = strict.get("/collections/")
        token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', page.content.decode()).group(1)
        assert strict.post("/collections/new", {"name": "kept", "csrfmiddlewaretoken": token}).status_code == 302


def test_reference_page_requests_one_part_and_shows_only_escaped_plain_text(web, uni):
    html = web.get("/search/", {"q": "kind:mail-message source:imap-fixture"}).content.decode()
    log_ref = unquote(next(r for r in _refs(html) if "Archive%2F2008" in r and "UID%3D2" in r))
    detail = web.get("/ref/", {"r": log_ref}).content.decode()
    assert "Lock keeper" in detail and "PARTS" in detail
    text_ref = log_ref + "#part=1"
    page = web.get("/ref/", {"r": text_ref}).content.decode()
    assert "not-requested" in page and "Request this part" in page
    assert web.post("/ref/request", {"r": text_ref}).status_code == 302
    assert "queued" in web.get("/ref/", {"r": text_ref}).content.decode()
    with closing(open_store(uni.config.store_dir, "queue", "connect")) as q:
        assert [tuple(r) for r in q.execute("SELECT part_id, requested_by, priority FROM content_requests")] == [
            ("1", "ui", "interactive")]


def test_fetched_text_is_escaped_untrusted_and_binary_parts_are_not_rendered(uni):
    with closing(open_store(uni.config.store_dir, "source", "web")) as db:
        log_native = db.execute("SELECT native_id FROM items WHERE subject LIKE 'Lock%'").fetchone()[0]
    nef = "INBOX;UIDVALIDITY=1700000001;UID=2"
    requests.request_part(uni.config.store_dir, "imap-fixture", log_native + "#part=1")
    requests.request_part(uni.config.store_dir, "imap-fixture", nef + "#part=2")
    assert connect.fetch_requests(uni.config)["fetched"] == 2
    configure(uni.config.store_dir, uni.config)
    with override_settings(TOWPATH_STORE_DIR=uni.config.store_dir, TOWPATH_CONFIG=uni.config,
                           ALLOWED_HOSTS=["testserver"], MIDDLEWARE=MIDDLEWARE):
        client = Client()
        text = client.get("/ref/", {"r": f"imap-fixture:{log_native}#part=1"}).content.decode()
        binary = client.get("/ref/", {"r": f"imap-fixture:{nef}#part=2"}).content.decode()
    assert "&lt;script&gt;alert(&#x27;log&#x27;)&lt;/script&gt;" in text and "<script>alert" not in text
    assert "Untrusted text copied from the source" in text and "IGNORE PREVIOUS INSTRUCTIONS" in text
    assert "only plain text is displayed" in binary and "synthetic raw sensor" not in binary


def test_reference_sets_survive_reindexing_and_report_unavailable_members(web, uni):
    assert web.post("/collections/new", {"name": "Aqueduct photos"}).status_code == 302
    cid = collections.list_all(uni.config.store_dir)[0]["collection_id"]
    found = federation.search(sources.build_adapters(uni.config), Filters.parse("extension:nef source:files-inventory"))
    for r in found.to_dict()["results"]:
        assert web.post(f"/collections/{cid}/add", {"r": r["ref"], "version": r["version"] or "",
                                                     "title": r["title"]}).status_code == 302
    before = collections.evaluate(sources.build_adapters(uni.config), collections.get(uni.config.store_dir, cid))
    assert before["counts"] == {"unchanged": 3} and before["partial"] is False
    # Regenerate the index from scratch: the collection lives elsewhere and its references are stable.
    (uni.config.store_dir / "files.db").unlink()
    with connect_role(uni):
        service.import_catalog(uni.config, "inventory")
    after = collections.evaluate(sources.build_adapters(uni.config), collections.get(uni.config.store_dir, cid))
    assert after["counts"] == {"unchanged": 3}
    # An entry missing from an inventory whose scope is incomplete is not proof of absence...
    manifest = uni.root / "files" / "manifests" / "photos.jsonl"
    lines = [line for line in manifest.read_text().splitlines() if "DSC_0043.nef" not in line]
    manifest.write_text("\n".join(lines) + "\n")
    with connect_role(uni):
        service.import_catalog(uni.config, "inventory")
    still = collections.evaluate(sources.build_adapters(uni.config), collections.get(uni.config.store_dir, cid))
    assert still["counts"] == {"unchanged": 3}
    # ...but once a complete inventory omits it, the member is unavailable, never silently replaced.
    lines = [line.replace('"complete": false', '"complete": true') for line in lines if "escape.NEF" not in line]
    manifest.write_text("\n".join(lines) + "\n")
    with connect_role(uni):
        assert service.import_catalog(uni.config, "inventory")[0]["marked_missing"] == 1
    page = web.get(f"/collections/{cid}/").content.decode()
    assert ">unavailable<" in page and "some members are unavailable now" in page


def test_query_collections_report_added_changed_and_removed_against_an_accepted_baseline(web, uni):
    assert web.post("/collections/new", {"name": "All NEF", "q": "extension:nef"}).status_code == 302
    cid = collections.list_all(uni.config.store_dir)[0]["collection_id"]
    first = web.get(f"/collections/{cid}/").content.decode()
    assert first.count(">added<") == 5 and "inventory of the declared scope is not known" in first
    assert web.post(f"/collections/{cid}/accept").status_code == 302
    assert len(collections.get(uni.config.store_dir, cid)["baseline"]) == 5
    manifest = uni.root / "files" / "manifests" / "photos.jsonl"
    text = manifest.read_text().replace('"2011-08-20T18:31:00Z"', '"2011-08-20T18:39:00Z"')
    text = "\n".join(line.replace('"complete": false', '"complete": true') for line in text.splitlines()
                     if "DSC_0043.nef" not in line and "escape.NEF" not in line) + "\n"
    manifest.write_text(text)  # a complete inventory now: one file changed, one gone
    with connect_role(uni):
        service.import_catalog(uni.config, "inventory")
    evaluation = collections.evaluate(sources.build_adapters(uni.config), collections.get(uni.config.store_dir, cid))
    statuses = {i["title"]: i["status"] for i in evaluation["items"]}
    assert statuses["DSC_0042.NEF"] == "changed" and statuses["DSC_0043.nef"] == "removed"
    assert statuses["DCIM/DSC_0001.NEF"] == "unchanged"
    # Gmail reports no version token, so its part is "unverified" rather than claimed unchanged.
    assert evaluation["partial"] is False
    assert evaluation["counts"] == {"changed": 1, "removed": 1, "unchanged": 2, "unverified": 1}


def test_collections_are_owner_decisions_in_the_decisions_store_only(web, uni):
    source_db = uni.config.store_dir / "source.db"
    files_db = uni.config.store_dir / "files.db"
    before = (hashlib.sha256(source_db.read_bytes()).hexdigest(), hashlib.sha256(files_db.read_bytes()).hexdigest())
    web.post("/collections/new", {"name": "Notes"})
    cid = collections.list_all(uni.config.store_dir)[0]["collection_id"]
    web.post(f"/collections/{cid}/add", {"r": "gmail-fixture:18d0000000000001", "note": "survey"})
    web.get(f"/collections/{cid}/")
    web.post(f"/collections/{cid}/remove", {"r": "gmail-fixture:18d0000000000001"})
    after = (hashlib.sha256(source_db.read_bytes()).hexdigest(), hashlib.sha256(files_db.read_bytes()).hexdigest())
    assert before == after
    with closing(open_store(uni.config.store_dir, "decisions", "connect")) as db:
        kinds = [r["kind"] for r in db.execute("SELECT kind FROM decision_log WHERE target_id LIKE 'collection:%'")]
    assert kinds == ["collection-create", "collection-add", "collection-remove"]


def test_new_routes_refuse_the_wrong_method_and_keep_security_headers(web):
    for path in ("/search/", "/ref/?r=gmail-fixture:x", "/collections/"):
        response = web.get(path)
        assert response.status_code in {200, 404}
        assert "script-src 'none'" in response["Content-Security-Policy"]
        assert web.post(path).status_code == 405
    for path in ("/search/request", "/ref/request", "/collections/new"):
        assert web.get(path).status_code == 405


def test_without_a_config_the_ui_searches_the_mail_sources_it_can_see(uni):
    configure(uni.config.store_dir, None)
    with override_settings(TOWPATH_STORE_DIR=uni.config.store_dir, TOWPATH_CONFIG=None,
                           ALLOWED_HOSTS=["testserver"], MIDDLEWARE=MIDDLEWARE):
        html = Client().get("/search/", {"q": "extension:nef"}).content.decode()
    assert "gmail-fixture" in html and "imap-fixture" in html and "files-inventory" not in html
    assert len(_refs(html)) == 2
