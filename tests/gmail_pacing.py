"""Two Gmail sources on fake services with the real limiter on a fake clock, for the sync hardening tests.

Nothing here contacts Google.
"""

import json
import random
from datetime import datetime, timezone

import pytest

pytest.importorskip("googleapiclient")
import httplib2  # noqa: E402
from googleapiclient.errors import HttpError  # noqa: E402

from tests.fake_clock import FakeClock  # noqa: E402
from tests.test_gmail_client import Call, FakeService  # noqa: E402
from towpath import config as config_mod  # noqa: E402
from towpath import quota  # noqa: E402
from towpath.adapters.google_gmail import GoogleGmailClient  # noqa: E402

START = datetime(2026, 10, 2, 19, 0, tzinfo=timezone.utc).timestamp()  # 12:00 in Los Angeles
CALL_METHOD = {"getProfile": "users.getProfile", "messages.list": "users.messages.list",
               "messages.get": "users.messages.get", "attachments.get": "users.messages.attachments.get",
               "history.list": "users.history.list"}


def http_error(status, reason=None, retry_after=None):
    headers = {"status": status}
    if retry_after is not None:
        headers["retry-after"] = str(retry_after)
    body = {"error": {"code": status, "message": "see https://example.invalid/should-not-appear",
                      "errors": [{"reason": reason}] if reason else []}}
    return HttpError(httplib2.Response(headers), json.dumps(body).encode())


def pace(ws, monkeypatch):
    """Two Gmail sources on fake services, with the real limiter on a fake clock."""
    (ws.root / "secrets").mkdir()
    for name in ("client_a", "client_b"):
        (ws.root / "secrets" / f"{name}.json").write_text("{}")
    ws.replace_config('adapter = "fixture-gmail"\npath = "account_a.json"',
                      'adapter = "gmail"\nclient_secrets = "secrets/client_a.json"\ntoken = "secrets/token_a.json"')
    ws.replace_config('adapter = "fixture-gmail"\npath = "account_b.json"',
                      'adapter = "gmail"\nclient_secrets = "secrets/client_b.json"\ntoken = "secrets/token_b.json"')
    services = {"token_a.json": FakeService(ws.root / "account_a.json"),
                "token_b.json": FakeService(ws.root / "account_b.json")}
    monkeypatch.setattr(GoogleGmailClient, "from_token", classmethod(
        lambda cls, path, **kw: cls(services[path.name], page_size=10, rng=random.Random(0), **kw)))
    clock = FakeClock(START)
    monkeypatch.setattr(quota, "CLOCK", clock)
    monkeypatch.setattr(quota, "SLEEP", clock.sleep)
    ws.service, ws.services, ws.clock = services["token_a.json"], services, clock
    return ws


def set_pacing(ws, **values):
    lines = "\n".join(f"{k} = {json.dumps(v)}" for k, v in values.items())
    path = ws.root / "towpath.toml"
    path.write_text(f"[gmail_pacing]\n{lines}\n\n" + path.read_text())
    ws.config = config_mod.load(path)


def attempts(ws, **where):
    db = ws.db("quota", "connect")
    sql = "SELECT * FROM attempts" + (" WHERE " + " AND ".join(f"{k} = ?" for k in where) if where else "")
    rows = [dict(r) for r in db.execute(sql + " ORDER BY seq", tuple(where.values()))]
    db.close()
    return rows


def run_row(ws, run_id):
    db = ws.db("source")
    row = dict(db.execute("SELECT r.*, c.termination AS cov_termination, c.items_seen FROM runs r "
                          "JOIN coverage c USING (run_id) WHERE run_id = ?", (run_id,)).fetchone())
    db.close()
    return row


def fail_get(ws, monkeypatch, plan, service=None):
    """Make messages.get fail per message ID according to ``plan[id]``: a list of errors, consumed in order."""
    service = service or ws.service
    original = service.get

    def get(**kw):
        errors = plan.get(kw["id"])
        if errors:
            exc = errors.pop(0)
            service.calls.append(("messages.get", kw))

            def raise_it():
                raise exc
            return Call(raise_it)
        return original(**kw)
    monkeypatch.setattr(service, "get", get)


def target(paced, n=0):
    return sorted(paced.service.data["messages"], reverse=True)[n]
