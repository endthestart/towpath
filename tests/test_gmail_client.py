"""The real Gmail client code path, against a fake of Google's discovery client.

This proves Towpath calls the API the way the design says (read-only calls,
``format=full`` with a structural field mask, history paging, 404 handling).
It does not prove how Gmail itself responds; that is the local check in
docs/setup/local-quickstart.md.
"""

import base64
import json

import pytest

pytest.importorskip("googleapiclient")
import httplib2  # noqa: E402
from googleapiclient.errors import HttpError  # noqa: E402

from towpath import connect, fieldmask  # noqa: E402
from towpath.adapters import google_gmail  # noqa: E402
from towpath.adapters.gmail import GmailConnector  # noqa: E402
from towpath.adapters.google_gmail import GoogleGmailClient, ScopeError, check_scopes, verify_structure  # noqa: E402

READONLY = google_gmail.READONLY_SCOPE


class Call:
    def __init__(self, fn):
        self.fn = fn

    def execute(self, num_retries=0):
        return self.fn()


class FakeService:
    """Mimics service.users().messages()/history() chains over a fixture file."""

    def __init__(self, path, ignore_mask=False):
        self.data = json.loads(path.read_text())
        self.ignore_mask = ignore_mask
        self.calls = []

    def _404(self):
        raise HttpError(httplib2.Response({"status": 404}), b'{"error": {"code": 404}}')

    def users(self):
        return self

    def messages(self):
        return self

    def attachments(self):
        return _Attachments(self)

    def history(self):
        return _History(self)

    def getProfile(self, userId):  # noqa: N802 - Google's method name
        self.calls.append(("getProfile", {"userId": userId}))
        return Call(lambda: {"emailAddress": self.data["emailAddress"], "historyId": self.data["historyId"]})

    def list(self, **kw):
        self.calls.append(("messages.list", kw))
        ids = sorted(self.data["messages"], reverse=True)
        if kw.get("q"):  # plain words against the synthetic raw message, standing in for Gmail's search
            ids = [i for i in ids if all(w.lower() in base64.urlsafe_b64decode(
                self.data["messages"][i]["raw"] + "==").decode("utf-8", "replace").lower() for w in kw["q"].split())]
        start = int(kw.get("pageToken") or 0)
        size = kw["maxResults"]

        def run():
            out = {"messages": [{"id": i, "threadId": self.data["messages"][i]["threadId"]}
                                for i in ids[start:start + size]]}
            if start + size < len(ids):
                out["nextPageToken"] = str(start + size)
            if kw.get("q"):
                out["resultSizeEstimate"] = len(ids)
            return out
        return Call(run)

    def get(self, **kw):
        self.calls.append(("messages.get", kw))

        def run():
            msg = self.data["messages"].get(kw["id"])
            if msg is None:
                self._404()
            msg = {k: v for k, v in msg.items() if k != "raw"}
            if kw.get("fields") and not self.ignore_mask:
                return fieldmask.apply(msg, fieldmask.parse(kw["fields"]))
            return msg
        return Call(run)


class _Attachments:
    def __init__(self, svc):
        self.svc = svc

    def get(self, **kw):
        self.svc.calls.append(("attachments.get", kw))
        data = self.svc.data["attachments"][kw["id"]]
        return Call(lambda: {"size": len(data), "data": data})


class _History:
    def __init__(self, svc):
        self.svc = svc

    def list(self, **kw):
        self.svc.calls.append(("history.list", kw))
        start = int(kw["startHistoryId"])

        def run():
            if start < int(self.svc.data["historyFloor"]):
                self.svc._404()
            recs = [h for h in self.svc.data["history"] if int(h["id"]) > start]
            page = int(kw.get("pageToken") or 0)
            out = {"history": recs[page:page + 2], "historyId": self.svc.data["historyId"]}
            if page + 2 < len(recs):
                out["nextPageToken"] = str(page + 2)
            return out
        return Call(run)


@pytest.fixture
def gmail_ws(ws, monkeypatch):
    (ws.root / "secrets").mkdir()
    (ws.root / "secrets" / "client.json").write_text("{}")
    ws.replace_config('adapter = "fixture-gmail"\npath = "account_a.json"',
                      'adapter = "gmail"\nclient_secrets = "secrets/client.json"\ntoken = "secrets/token.json"')
    service = FakeService(ws.root / "account_a.json")
    monkeypatch.setattr(GoogleGmailClient, "from_token",
                        classmethod(lambda cls, path, **kw: cls(service, page_size=10)))
    ws.service = service
    return ws


def test_sync_uses_only_read_calls_with_structural_mask(gmail_ws):
    result = connect.sync(gmail_ws.config, "src_a")
    assert result["complete"] and result["indexed_total"] == gmail_ws.summary["messages_a"]
    kinds = {c[0] for c in gmail_ws.service.calls}
    assert kinds <= {"getProfile", "messages.list", "messages.get", "attachments.get", "history.list"}
    gets = [kw for name, kw in gmail_ws.service.calls if name == "messages.get"]
    assert all(kw["format"] == "full" and kw["fields"] == fieldmask.message_mask() for kw in gets)
    assert "data" not in fieldmask.message_mask()
    lists = [kw for name, kw in gmail_ws.service.calls if name == "messages.list"]
    assert all(kw["includeSpamTrash"] is False for kw in lists)


def test_incremental_paging_and_expired_cursor(gmail_ws):
    from towpath.fixtures import generator

    connect.sync(gmail_ws.config, "src_a")
    gmail_ws.advance()
    gmail_ws.service.data = json.loads((gmail_ws.root / "account_a.json").read_text())
    inc = connect.sync(gmail_ws.config, "src_a")
    assert inc["kind"] == "incremental" and inc["absent"] == 1 and inc["new"] == 2
    pages = [kw for name, kw in gmail_ws.service.calls if name == "history.list"]
    assert len(pages) > 1  # followed nextPageToken
    generator.expire_cursor(gmail_ws.root)
    gmail_ws.service.data = json.loads((gmail_ws.root / "account_a.json").read_text())
    again = connect.sync(gmail_ws.config, "src_a")
    assert again["kind"] == "full-after-expired-cursor" and again["complete"]


@pytest.mark.parametrize("change_type", ["labelsAdded", "labelsRemoved"])
def test_incremental_labels_use_history_deltas(gmail_ws, change_type):
    connect.sync(gmail_ws.config, "src_a")
    message_id = gmail_ws.summary["newsletters"][0]
    message = gmail_ws.service.data["messages"][message_id]
    original_labels = set(message["labelIds"])
    changed_label = "Label_Checked" if change_type == "labelsAdded" else "UNREAD"
    expected = (original_labels | {changed_label} if change_type == "labelsAdded"
                else original_labels - {changed_label})
    history_id = str(int(gmail_ws.service.data["historyId"]) + 1)
    gmail_ws.service.data["historyId"] = history_id
    gmail_ws.service.data["history"].append({"id": history_id, change_type: [{
        "message": {"id": message_id, "threadId": message["threadId"]},
        "labelIds": [changed_label],
    }]})
    result = connect.sync(gmail_ws.config, "src_a")
    assert result["complete"] and result["kind"] == "incremental"
    db = gmail_ws.db("source")
    row = db.execute("SELECT labels FROM observations WHERE item_id = ? AND run_id = ?",
                     (gmail_ws.item(message_id), result["run_id"])).fetchone()
    db.close()
    assert set(json.loads(row["labels"])) == expected


def test_fetch_and_scan_through_real_client(gmail_ws):
    gmail_ws.full_pipeline()
    att = [kw for name, kw in gmail_ws.service.calls if name == "attachments.get"]
    assert att and all(kw["userId"] == "me" for kw in att)
    from towpath import scan
    names = {p["body"]["file_name"] for p in scan.list_proposals(gmail_ws.config, "proposed")}
    assert "receipt-small.pdf" in names  # inline-data part fetched via the full message


def test_max_items_caps_and_resumes(gmail_ws):
    first = connect.sync(gmail_ws.config, "src_a", max_items=10)
    assert first["complete"] is False and first["indexed_total"] == 10
    newest = sorted(gmail_ws.service.data["messages"], reverse=True)[:10]
    db = gmail_ws.db("source")
    indexed = {r[0] for r in db.execute("SELECT native_id FROM items")}
    db.close()
    assert indexed == set(newest)  # newest first
    second = connect.sync(gmail_ws.config, "src_a")
    assert second["complete"] and second["indexed_total"] == gmail_ws.summary["messages_a"]


def test_verify_structure_reports(gmail_ws):
    client = GoogleGmailClient(gmail_ws.service, page_size=10)
    mask = GmailConnector("src_a", client).mask
    good = verify_structure(client, mask, sample=40)
    assert good["passed"] and good["messages_checked"] == gmail_ws.summary["messages_a"]
    assert good["attachments_inline"] == 1 and good["max_depth"] == 4
    gmail_ws.service.ignore_mask = True
    bad = verify_structure(client, mask, sample=40)
    assert not bad["passed"] and bad["messages_with_body_data"] > 0
    gmail_ws.service.ignore_mask = False
    shallow = verify_structure(client, fieldmask.message_mask(2), sample=40)
    assert not shallow["passed"] and shallow["truncated"] > 0


def test_verify_structure_requires_messages(gmail_ws):
    gmail_ws.service.data["messages"] = {}
    report = verify_structure(GoogleGmailClient(gmail_ws.service), fieldmask.message_mask(), sample=50)
    assert report["messages_checked"] == 0
    assert not report["passed"]
    assert report["reason"] == "no messages were checked; structure verification needs a non-empty mailbox"


@pytest.mark.parametrize("sample", [0, -1])
def test_verify_structure_rejects_nonpositive_samples(gmail_ws, sample):
    with pytest.raises(ValueError, match="sample must be at least 1"):
        verify_structure(GoogleGmailClient(gmail_ws.service), fieldmask.message_mask(), sample=sample)
    assert gmail_ws.service.calls == []


@pytest.mark.parametrize("rescan", [False, True])
def test_full_sync_handles_message_deleted_after_listing(gmail_ws, monkeypatch, rescan):
    if rescan:
        from towpath.fixtures import generator

        connect.sync(gmail_ws.config, "src_a")
        generator.expire_cursor(gmail_ws.root)
        gmail_ws.service.data = json.loads((gmail_ws.root / "account_a.json").read_text())
    missing_id = sorted(gmail_ws.service.data["messages"], reverse=True)[0]
    original = gmail_ws.service.get

    def deleted_after_listing(**kw):
        if kw["id"] == missing_id:
            return Call(gmail_ws.service._404)
        return original(**kw)

    monkeypatch.setattr(gmail_ws.service, "get", deleted_after_listing)
    result = connect.sync(gmail_ws.config, "src_a")
    assert result["complete"]
    assert result["indexed_total"] == gmail_ws.summary["messages_a"] - 1
    db = gmail_ws.db("source")
    row = db.execute("SELECT absent_since_run FROM items WHERE native_id = ?", (missing_id,)).fetchone()
    db.close()
    if rescan:
        assert row["absent_since_run"] == result["run_id"]
    else:
        assert row is None


def test_full_sync_does_not_hide_server_errors(gmail_ws, monkeypatch):
    def server_error(**kw):
        def fail():
            raise HttpError(httplib2.Response({"status": 500}), b'{"error": {"code": 500}}')
        return Call(fail)

    monkeypatch.setattr(gmail_ws.service, "get", server_error)
    # Server errors are retried, then end the run as a recorded, resumable stop; never as "message missing".
    result = connect.sync(gmail_ws.config, "src_a")
    assert result["termination"] == "server-stop" and result["complete"] is False
    assert "://" not in result["reason"] and "googleapis" not in result["reason"]
    db = gmail_ws.db("source")
    assert db.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0
    run = db.execute("SELECT termination, complete FROM runs WHERE run_id = ?", (result["run_id"],)).fetchone()
    db.close()
    assert tuple(run) == ("server-stop", 0)


def test_scope_checks():
    check_scopes({READONLY})
    with pytest.raises(ScopeError):
        check_scopes({READONLY, "https://www.googleapis.com/auth/gmail.modify"})
    with pytest.raises(ScopeError):
        check_scopes({"https://www.googleapis.com/auth/gmail.metadata"})


def test_real_client_has_no_write_calls():
    public = {n for n in dir(GoogleGmailClient) if not n.startswith("_")}
    assert public == {"from_token", "get_profile", "list_messages", "get_message", "get_attachment", "list_history",
                      "close"}  # close releases the quota budget lock


@pytest.mark.parametrize("existing_mode", [None, 0o644, 0o666])
def test_token_file_is_private(tmp_path, existing_mode):
    path = tmp_path / "secrets" / "token.json"
    if existing_mode is not None:
        path.parent.mkdir()
        path.write_text('{"old":"synthetic"}')
        path.chmod(existing_mode)
    google_gmail._write_private(path, "{}")
    assert (path.stat().st_mode & 0o777) == 0o600
    assert path.read_text() == "{}"


def test_failed_token_replacement_preserves_previous_file(tmp_path, monkeypatch):
    path = tmp_path / "token.json"
    path.write_text('{"old":"synthetic"}')
    path.chmod(0o600)

    def fail_replace(*args):
        raise OSError("synthetic replacement failure")

    monkeypatch.setattr(google_gmail.os, "replace", fail_replace)
    with pytest.raises(OSError, match="synthetic replacement failure"):
        google_gmail._write_private(path, '{"new":"synthetic"}')
    assert path.read_text() == '{"old":"synthetic"}'
    assert list(tmp_path.iterdir()) == [path]


def test_provider_search_uses_q_under_the_same_scope_client_and_budget(gmail_ws, monkeypatch):
    from towpath.stores import open_store
    from towpath.unified import federation, sources
    from towpath.unified.contracts import Filters

    service = gmail_ws.service
    monkeypatch.setattr(GoogleGmailClient, "from_token",
                        classmethod(lambda cls, path, **kw: cls(service, page_size=10, limiter=kw.get("limiter"))))
    connect.sync(gmail_ws.config, "src_a")
    service.calls.clear()
    adapters = {"src_a": sources.MailAdapter(gmail_ws.config, gmail_ws.config.sources["src_a"], connect=True)}
    try:
        resp = federation.search(adapters, Filters.parse("statement"), limit=3).to_dict()
    finally:
        federation.close_all(adapters)
    page = resp["sources"][0]
    assert page["status"] == "ok" and page["depth"] == "provider-search" and len(resp["results"]) == 3
    assert page["next_cursor"] == "3" and page["estimate"]["value"] == 6
    lists = [kw for name, kw in service.calls if name == "messages.list"]
    assert lists == [{"userId": "me", "pageToken": None, "maxResults": 3, "includeSpamTrash": False,
                      "fields": "messages(id,threadId),nextPageToken,resultSizeEstimate", "q": "statement"}]
    assert not [c for c in service.calls if c[0] == "messages.get"]  # indexed matches need no extra reads
    quota = open_store(gmail_ws.config.store_dir, "quota", "connect")
    try:
        methods = [(r["method"], r["units"]) for r in quota.execute("SELECT method, units FROM attempts")]
    finally:
        quota.close()
    assert methods[-1] == ("users.messages.list", 5)  # accounted and paced like every other call
