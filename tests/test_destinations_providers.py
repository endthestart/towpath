"""Paperless and Immich lookups and the Inbox Zero read-only adapter, against loopback stubs.

These prove Towpath's request shapes and flow, not the real services' behavior;
docs/setup/local-quickstart.md lists the checks against real instances.
"""

import base64
import hashlib

import pytest

from towpath import connect, scan
from towpath.providers.inbox_zero import InboxZeroProvider
from tests.stubs import ImmichStub, InboxZeroStub, PaperlessStub


@pytest.fixture
def services(ws, monkeypatch):
    docs = (ws.root / "documents" / "statement-2026-03.pdf").read_bytes()
    photo = (ws.root / "photos" / "photo-2.png").read_bytes()
    paperless = PaperlessStub({hashlib.sha256(docs).hexdigest(): 101})
    immich = ImmichStub({base64.b64encode(hashlib.sha1(photo).digest()).decode(): "asset-7f3e"})
    monkeypatch.setenv("TP_PAPERLESS", "paperless-secret")
    monkeypatch.setenv("TP_IMMICH", "immich-secret")
    text = (ws.root / "towpath.toml").read_text()
    text = text.replace('destination = "dst_documents"', 'destination = "dst_paperless"')
    text = text.replace('destination = "dst_photos"', 'destination = "dst_immich"')
    text += f"""
[[sources]]
id = "dst_paperless"
kind = "document-system"
adapter = "paperless"
base_url = "{paperless.url}"
token = "env:TP_PAPERLESS"

[[sources]]
id = "dst_immich"
kind = "photo-library"
adapter = "immich"
base_url = "{immich.url}"
token = "env:TP_IMMICH"
"""
    (ws.root / "towpath.toml").write_text(text)
    ws.replace_config("", "")
    yield ws, paperless, immich
    paperless.close()
    immich.close()


def _run(ws):
    ws.sync_all()
    ws.scan_all()
    connect.fetch_requests(ws.config)  # content
    first = ws.scan_all()              # presence requests
    connect.fetch_requests(ws.config)  # lookups
    return first, ws.scan_all()


def test_lookup_destinations_flow(services):
    ws, paperless, immich = services
    first, final = _run(ws)
    assert first["pdf-to-documents"]["waiting"] == first["pdf-to-documents"]["candidates"]
    assert final["pdf-to-documents"]["algorithm"] == "sha256" and final["pdf-to-documents"]["present"] == 1
    assert final["photos-to-library"]["algorithm"] == "sha1-base64" and final["photos-to-library"]["present"] == 1
    names = {p["body"]["file_name"]: p["body"]["destination_id"] for p in scan.list_proposals(ws.config, "proposed")}
    assert "statement-2026-03.pdf" not in names and "photo-2.png" not in names
    assert names["statement-2026-01.pdf"] == "dst_paperless" and names["photo-1.png"] == "dst_immich"
    assert {r["method"] for r in paperless.requests} == {"GET"}
    assert {(r["method"], r["path"]) for r in immich.requests} <= {("GET", "/api/server/version"),
                                                                     ("POST", "/api/search/metadata")}
    db = ws.db("source")
    found = db.execute("SELECT remote_id FROM destination_lookups WHERE present = 1 ORDER BY source_id").fetchall()
    db.close()
    assert [r[0] for r in found] == ["asset-7f3e", "101"]
    _, again = (connect.fetch_requests(ws.config), ws.scan_all())
    assert again["pdf-to-documents"]["proposed"] == final["pdf-to-documents"]["proposed"]


def test_paperless_v2_uses_md5(services):
    ws, paperless, _ = services
    paperless.version = "2.20.0"
    paperless.held = {hashlib.md5((ws.root / "documents" / "statement-2026-03.pdf").read_bytes()).hexdigest(): 7}
    _, final = _run(ws)
    assert final["pdf-to-documents"]["algorithm"] == "md5" and final["pdf-to-documents"]["present"] == 1


def test_wrong_token_fails_loudly(services, monkeypatch):
    ws, _, _ = services
    monkeypatch.setenv("TP_PAPERLESS", "wrong")
    from towpath.http import HttpStatusError
    with pytest.raises(HttpStatusError):
        connect.sync(ws.config, "dst_paperless")


def test_inbox_zero_read_only_adapter(ws, monkeypatch):
    stub = InboxZeroStub({"full": {"STATS_READ", "RULES_READ"}, "stats-only": {"STATS_READ"}})
    monkeypatch.setenv("TP_IZ", "full")
    ws.add_config(f"""
[[providers]]
id = "iz"
kind = "mail-management"
adapter = "inbox-zero"
base_url = "{stub.url}"
api_key = "env:TP_IZ"
links = {{ account_id = "acct-123" }}
""")
    p = InboxZeroProvider(ws.config.providers["iz"])
    assert p.probe() == {"overview": "ok", "rules": "ok"}
    overview = p.overview("month")
    assert overview["by_period"]["period"] == "month" and overview["response_time"]["summary"]["within1Hour"] == 0.5
    assert p.rules()[0]["name"] == "Newsletters"
    links = p.links()
    assert links["important_unanswered"] == f"{stub.url}/acct-123/reply-zero"
    monkeypatch.setenv("TP_IZ", "stats-only")
    limited = InboxZeroProvider(ws.config.providers["iz"])
    assert limited.probe() == {"overview": "ok", "rules": "unavailable (HTTP 403)"}
    assert {r["method"] for r in stub.requests} == {"GET"}
    with pytest.raises(ValueError):
        p.overview("fortnight")
    stub.close()
