"""Gmail sync hardening (docs/evaluations/gmail-sync-hardening.md), on synthetic data only.

A fake clock replaces real time, and a fake of Google's discovery client
returns Google-shaped responses and errors. Nothing here contacts Google.
"""

import fcntl
import json
import os
import random
from datetime import datetime, timezone

import pytest

pytest.importorskip("googleapiclient")
import httplib2  # noqa: E402
from googleapiclient.errors import HttpError  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

from tests.test_gmail_client import Call, FakeService  # noqa: E402
from towpath import config as config_mod  # noqa: E402
from towpath import connect, fieldmask, quota  # noqa: E402
from towpath.adapters import build_connector  # noqa: E402
from towpath.adapters.errors import LockBusy  # noqa: E402
from towpath.adapters.google_gmail import GoogleGmailClient  # noqa: E402
from towpath.fixtures import generator  # noqa: E402
from towpath.progress import Progress  # noqa: E402
from towpath.quota import METHOD_COSTS, BudgetLock, PacingSettings, QuotaLimiter  # noqa: E402

START = datetime(2026, 10, 2, 19, 0, tzinfo=timezone.utc).timestamp()  # 12:00 in Los Angeles
CALL_METHOD = {"getProfile": "users.getProfile", "messages.list": "users.messages.list",
               "messages.get": "users.messages.get", "attachments.get": "users.messages.attachments.get",
               "history.list": "users.history.list"}


class FakeClock:
    def __init__(self, now=START):
        self.now = now
        self.sleeps: list[float] = []
        self.cancel_on_sleep: int | None = None

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        if self.cancel_on_sleep is not None and len(self.sleeps) >= self.cancel_on_sleep:
            raise KeyboardInterrupt
        self.now += seconds


def http_error(status, reason=None, retry_after=None):
    headers = {"status": status}
    if retry_after is not None:
        headers["retry-after"] = str(retry_after)
    body = {"error": {"code": status, "message": "see https://example.invalid/should-not-appear",
                      "errors": [{"reason": reason}] if reason else []}}
    return HttpError(httplib2.Response(headers), json.dumps(body).encode())


@pytest.fixture
def paced(ws, monkeypatch):
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
    clock = FakeClock()
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


# -- pacing, accounting, and budgets ---------------------------------------------

def test_every_attempt_is_accounted_at_its_method_cost(paced):
    result = connect.sync(paced.config, "src_a")
    assert result["complete"]
    rows = attempts(paced, account="src_a")
    calls = [CALL_METHOD[name] for name, _ in paced.service.calls]
    assert [r["method"] for r in rows] == calls
    assert all(r["units"] == METHOD_COSTS[r["method"]] for r in rows)
    assert sum(r["units"] for r in rows) == sum(METHOD_COSTS[m] for m in calls)


def test_minimum_interval_and_no_startup_burst_across_restarts(paced):
    connect.sync(paced.config, "src_a", max_items=5)
    times = [r["at"] for r in attempts(paced)]
    assert all(b - a >= 1.0 for a, b in zip(times, times[1:], strict=False))
    # A new command right away must not burst: its first request waits for the persisted interval.
    paced.clock.sleeps.clear()
    connect.sync(paced.config, "src_a", max_items=1)
    assert paced.clock.sleeps and paced.clock.sleeps[0] == pytest.approx(1.0)
    times = [r["at"] for r in attempts(paced)]
    assert all(b - a >= 1.0 for a, b in zip(times, times[1:], strict=False))


def test_per_minute_budget_holds_in_every_window(paced):
    set_pacing(paced, units_per_minute=100)
    snaps = []
    connect.sync(paced.config, "src_a", max_items=12, progress=Progress("src_a", snaps.append, clock=paced.clock))
    rows = attempts(paced, account="src_a")
    for row in rows:
        window = sum(r["units"] for r in rows if row["at"] - 60 < r["at"] <= row["at"])
        assert window <= 100
    assert "per-minute budget" in {s.get("wait_reason") for s in snaps}


def test_daily_budget_is_shared_persistent_and_resumes_next_day(paced):
    set_pacing(paced, daily_units=300)
    first = connect.sync(paced.config, "src_a")
    assert first["termination"] == "daily-budget-stop" and first["exit_code"] == 3 and not first["complete"]
    assert first["indexed_total"] > 0
    used = sum(r["units"] for r in attempts(paced))
    assert used <= 300
    before = len(attempts(paced))
    second = connect.sync(paced.config, "src_b")  # same budget, different account
    assert second["termination"] == "daily-budget-stop" and second["indexed_total"] == 0
    assert sum(r["units"] for r in attempts(paced)) <= 300  # at most a 1-unit profile call fit
    assert not [r for r in attempts(paced)[before:] if r["method"] == "users.messages.get"]
    paced.clock.now = datetime(2026, 10, 3, 7, 1, tzinfo=timezone.utc).timestamp()  # 00:01 in Los Angeles
    resumed = connect.sync(paced.config, "src_a")
    assert resumed["kind"] == "resumed-full" and resumed["seen"] > 0
    assert resumed["termination"] in {"complete", "daily-budget-stop"}
    assert resumed["indexed_total"] > first["indexed_total"]


def test_lock_prevents_two_commands_spending_one_budget(paced):
    path = paced.config.store_dir / "locks" / "gmail-default.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)  # stands in for another process
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        assert BudgetLock.is_held_elsewhere(paced.config.store_dir, "default")
        with pytest.raises(LockBusy):
            connect.sync(paced.config, "src_a")
        assert attempts(paced) == []
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    assert connect.sync(paced.config, "src_a")["complete"]
    assert BudgetLock._held == {}


# -- retries and error classes ------------------------------------------------------

def target(paced, n=0):
    return sorted(paced.service.data["messages"], reverse=True)[n]


def test_retry_after_is_honored_and_retries_are_accounted(paced, monkeypatch):
    mid = target(paced, 2)
    fail_get(paced, monkeypatch, {mid: [http_error(429, retry_after=7)]})
    result = connect.sync(paced.config, "src_a")
    assert result["complete"]
    assert 7.0 in paced.clock.sleeps
    gets = [r for r in attempts(paced) if r["method"] == "users.messages.get"]
    assert len(gets) == paced.summary["messages_a"] + 1  # every message once, plus the one retry
    assert len([c for c in paced.service.calls if c[0] == "messages.get" and c[1]["id"] == mid]) == 2


def test_quota_errors_back_off_with_jitter_then_stop_cleanly(paced, monkeypatch):
    mid = target(paced, 3)
    fail_get(paced, monkeypatch, {mid: [http_error(403, "rateLimitExceeded") for _ in range(6)]})
    result = connect.sync(paced.config, "src_a")
    assert result["termination"] == "quota-stop" and result["exit_code"] == 3 and not result["complete"]
    assert "://" not in result["reason"] and "example.invalid" not in result["reason"]
    assert "rateLimitExceeded" in result["reason"]
    backoffs = [s for s in paced.clock.sleeps if s > 1.0]
    assert len(backoffs) == 5
    for attempt, wait in enumerate(backoffs, start=1):
        ceiling = min(300.0, 2.0 * 2 ** (attempt - 1))
        assert ceiling / 2 <= wait <= ceiling
    tries = [c for c in paced.service.calls if c[0] == "messages.get" and c[1]["id"] == mid]
    assert len(tries) == 6
    row = run_row(paced, result["run_id"])
    assert row["termination"] == row["cov_termination"] == "quota-stop" and row["complete"] == 0
    assert row["items_processed"] == 3


def test_long_retry_after_stops_instead_of_waiting(paced, monkeypatch):
    fail_get(paced, monkeypatch, {target(paced): [http_error(429, retry_after=4000)]})
    result = connect.sync(paced.config, "src_a")
    assert result["termination"] == "quota-stop" and "longer than backoff_max_seconds" in result["reason"]
    assert max(paced.clock.sleeps or [0]) < 300


@pytest.mark.parametrize("error,termination,code", [
    (http_error(401, "authError"), "auth-stop", 4),
    (http_error(403, "insufficientPermissions"), "permission-stop", 5),
    (http_error(400, "invalidArgument"), "request-stop", 6),
])
def test_non_retryable_errors_stop_after_one_attempt(paced, monkeypatch, error, termination, code):
    mid = target(paced)
    fail_get(paced, monkeypatch, {mid: [error]})
    result = connect.sync(paced.config, "src_a")
    assert result["termination"] == termination and result["exit_code"] == code
    assert len([c for c in paced.service.calls if c[0] == "messages.get" and c[1]["id"] == mid]) == 1
    assert "://" not in result["reason"]


def test_server_and_network_errors_are_retried(paced, monkeypatch):
    fail_get(paced, monkeypatch, {target(paced): [http_error(503, "backendError"), ConnectionResetError()]})
    assert connect.sync(paced.config, "src_a")["complete"]


def test_missing_message_is_not_an_error(paced, monkeypatch):
    fail_get(paced, monkeypatch, {target(paced): [http_error(404, "notFound")]})
    result = connect.sync(paced.config, "src_a")
    assert result["complete"] and result["indexed_total"] == paced.summary["messages_a"]  # read on reconcile


# -- clean stops, cancellation, and recorded coverage -----------------------------

def test_ctrl_c_while_waiting_is_recorded_and_releases_everything(paced):
    paced.clock.cancel_on_sleep = 4
    with pytest.raises(KeyboardInterrupt):
        connect.sync(paced.config, "src_a")
    db = paced.db("source")
    run = dict(db.execute("SELECT * FROM runs ORDER BY seq DESC LIMIT 1").fetchone())
    cov = db.execute("SELECT termination FROM coverage WHERE run_id = ?", (run["run_id"],)).fetchone()
    state = dict(db.execute("SELECT * FROM sync_state WHERE source_id = 'src_a'").fetchone())
    db.close()
    assert run["termination"] == "cancelled" and run["complete"] == 0 and cov[0] == "cancelled"
    assert state["full_sync_history_id"] and state["cursor"] is None
    assert BudgetLock._held == {}
    paced.clock.cancel_on_sleep = None
    assert connect.sync(paced.config, "src_a")["complete"]


def test_unexpected_error_is_recorded_sanitized_and_reraised(paced, monkeypatch):
    from towpath.adapters.gmail import GmailConnector
    original, calls = GmailConnector._record, {"n": 0}

    def flaky(self, msg):
        calls["n"] += 1
        if calls["n"] == 3:
            raise ValueError("synthetic failure mentioning person@example.com")
        return original(self, msg)

    monkeypatch.setattr(GmailConnector, "_record", flaky)
    with pytest.raises(ValueError):
        connect.sync(paced.config, "src_a")
    db = paced.db("source")
    run = dict(db.execute("SELECT * FROM runs ORDER BY seq DESC LIMIT 1").fetchone())
    db.close()
    assert run["termination"] == "error" and "ValueError" in run["reason"] and "@" not in run["reason"]
    assert BudgetLock._held == {}


def test_capped_run_is_never_complete_and_keeps_checkpoint(paced):
    result = connect.sync(paced.config, "src_a", max_items=7)
    assert result["termination"] == "capped" and not result["complete"]
    db = paced.db("source")
    state = dict(db.execute("SELECT * FROM sync_state WHERE source_id = 'src_a'").fetchone())
    db.close()
    assert state["full_sync_history_id"] == paced.service.data["historyId"] and state["cursor"] is None
    again = connect.sync(paced.config, "src_a")
    assert again["complete"] and again["indexed_total"] == paced.summary["messages_a"]


# -- resume efficiency and correctness ----------------------------------------------

def test_resume_starts_at_the_saved_page_not_the_first(paced, monkeypatch):
    mid = target(paced, 25)  # on the third page of ten
    fail_get(paced, monkeypatch, {mid: [http_error(429, "rateLimitExceeded") for _ in range(6)]})
    stopped = connect.sync(paced.config, "src_a")
    assert stopped["termination"] == "quota-stop"
    paced.service.calls.clear()
    resumed = connect.sync(paced.config, "src_a")
    assert resumed["complete"] and resumed["indexed_total"] == paced.summary["messages_a"]
    first_list = next(kw for name, kw in paced.service.calls if name == "messages.list")
    assert first_list["pageToken"] == "20"
    db = paced.db("source")
    assert db.execute("SELECT COUNT(*) FROM items").fetchone()[0] == paced.summary["messages_a"]
    assert db.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == paced.summary["messages_a"]
    db.close()


def test_invalid_saved_page_token_restarts_listing_without_duplicates(paced, monkeypatch):
    connect.sync(paced.config, "src_a", max_items=15)
    original, stale = paced.service.list, {"pending": True}

    def strict_list(**kw):
        if kw.get("pageToken") == "10" and stale.pop("pending", False):  # only the saved token is stale
            paced.service.calls.append(("messages.list", kw))

            def reject():
                raise http_error(400, "invalidArgument")
            return Call(reject)
        return original(**kw)

    monkeypatch.setattr(paced.service, "list", strict_list)
    resumed = connect.sync(paced.config, "src_a")
    assert resumed["complete"] and resumed["indexed_total"] == paced.summary["messages_a"]
    reads = [kw["id"] for name, kw in paced.service.calls if name == "messages.get"]
    assert len(reads) == len(set(reads))


def test_mailbox_shift_during_scan_is_caught_by_reconcile(paced, monkeypatch):
    ids = sorted(paced.service.data["messages"], reverse=True)
    deleted, shifted = ids[3], ids[10]
    original, state = paced.service.list, {"n": 0}

    def shifting_list(**kw):
        state["n"] += 1
        page = original(**kw).execute()
        if state["n"] == 1:
            del paced.service.data["messages"][deleted]  # offsets shift: page two now skips `shifted`
        return Call(lambda: page)

    monkeypatch.setattr(paced.service, "list", shifting_list)
    result = connect.sync(paced.config, "src_a")
    assert result["complete"] and result["indexed_total"] == paced.summary["messages_a"] - 1
    db = paced.db("source")
    indexed = {r[0] for r in db.execute("SELECT native_id FROM items WHERE absent_since_run IS NULL")}
    db.close()
    assert shifted in indexed and deleted not in indexed


def test_partial_listing_is_not_evidence_of_absence(paced, monkeypatch):
    connect.sync(paced.config, "src_a")
    generator.expire_cursor(paced.root)
    paced.service.data = json.loads((paced.root / "account_a.json").read_text())
    hidden = target(paced, 5)
    original, state = paced.service.list, {"n": 0}

    def flaky_list(**kw):
        state["n"] += 1
        page = original(**kw).execute()
        if state["n"] > 4:  # the reconcile pass omits a message that still exists
            page = dict(page, messages=[m for m in page["messages"] if m["id"] != hidden])
        return Call(lambda: page)

    monkeypatch.setattr(paced.service, "list", flaky_list)
    result = connect.sync(paced.config, "src_a")
    assert result["complete"] and result["absent"] == 0
    reads = [kw["id"] for name, kw in paced.service.calls if name == "messages.get"]
    assert reads.count(hidden) >= 2  # read during the rescan and once more to confirm


def test_incremental_checkpoints_never_pass_unprocessed_events(paced, monkeypatch):
    connect.sync(paced.config, "src_a")
    summary = paced.advance()
    paced.service.data = json.loads((paced.root / "account_a.json").read_text())
    history = paced.service.data["history"]
    added_record = next(h for h in history if "messagesAdded" in h)
    earlier = [h["id"] for h in history if int(h["id"]) < int(added_record["id"])]
    fail_get(paced, monkeypatch, {summary["added"][0]: [http_error(429, "rateLimitExceeded") for _ in range(6)]})
    stopped = connect.sync(paced.config, "src_a")
    assert stopped["termination"] == "quota-stop" and stopped["kind"] == "incremental"
    db = paced.db("source")
    cursor = db.execute("SELECT cursor FROM sync_state WHERE source_id = 'src_a'").fetchone()[0]
    db.close()
    assert cursor == max(earlier, key=int)  # the last record fully processed, never past the stopped one
    resumed = connect.sync(paced.config, "src_a")
    assert resumed["complete"] and resumed["cursor"] == paced.service.data["historyId"]
    db = paced.db("source")
    for native in summary["relabeled"]:
        rows = db.execute("SELECT labels FROM observations WHERE item_id = ? ORDER BY run_id",
                          (connect.item_id("src_a", native),)).fetchall()
        assert json.loads(rows[-1][0]) == ["CATEGORY_PROMOTIONS", "Label_Newsletters"]
    assert db.execute("SELECT absent_since_run IS NOT NULL FROM items WHERE native_id = ?",
                      (summary["deleted"],)).fetchone()[0] == 1
    db.close()


def test_sync_never_fetches_content_and_masks_exclude_body_data(paced):
    connect.sync(paced.config, "src_a")
    generator.expire_cursor(paced.root)
    paced.service.data = json.loads((paced.root / "account_a.json").read_text())
    connect.sync(paced.config, "src_a")
    assert not [c for c in paced.service.calls if c[0] == "attachments.get"]
    masks = {kw["fields"] for name, kw in paced.service.calls if name == "messages.get"}
    assert masks == {fieldmask.message_mask()} and "data" not in fieldmask.message_mask()


# -- defaults, configuration, progress, status, CLI ---------------------------------

def test_ordinary_cli_path_gets_the_default_limiter(paced):
    assert paced.config.gmail_pacing == PacingSettings()
    connector = build_connector(paced.config.sources["src_a"], paced.config)
    try:
        assert isinstance(connector.limiter, QuotaLimiter) and connector.client.limiter is connector.limiter
        s = connector.limiter.settings
        assert (s.min_interval_seconds, s.units_per_minute, s.daily_units) == (1.0, 1200, 1_800_000)
    finally:
        connector.close()
    assert BudgetLock._held == {}


@pytest.mark.parametrize("values,ok", [
    ({"units_per_minute": 600, "min_interval_seconds": 2.0, "daily_units": 500_000}, True),
    ({"units_per_minute": 1300}, False),
    ({"min_interval_seconds": 0.5}, False),
    ({"daily_units": 2_000_000}, False),
    ({"budget_id": "../escape"}, False),
    ({"day_timezone": "Not/AZone"}, False),
    ({"bursts": 5}, False),
])
def test_pacing_settings_allow_only_lower_rates(ws, values, ok):
    if ok:
        set_pacing(ws, **values)
        assert ws.config.gmail_pacing.units_per_minute == values["units_per_minute"]
    else:
        with pytest.raises(config_mod.ConfigError):
            set_pacing(ws, **values)


@pytest.mark.parametrize("values,ok", [
    ({"verified_units_per_minute": 6000, "units_per_minute": 1800, "min_interval_seconds": 0.667}, True),
    ({"verified_units_per_minute": 6000, "units_per_minute": 1801}, False),
    ({"verified_units_per_minute": 4000, "units_per_minute": 1201}, False),
    ({"verified_units_per_minute": 15000, "units_per_minute": 1801}, False),
    ({"verified_units_per_minute": 6000, "min_interval_seconds": 0.49}, False),
    ({"verified_units_per_minute": 6000, "daily_units": 1800001}, False),
    ({"verified_units_per_minute": 0}, False),
    ({"verified_units_per_minute": True}, False),
])
def test_verified_quota_opt_in_is_limited_to_thirty_percent(ws, values, ok):
    if ok:
        set_pacing(ws, **values)
        assert ws.config.gmail_pacing.verified_units_per_minute == values["verified_units_per_minute"]
    else:
        with pytest.raises(config_mod.ConfigError):
            set_pacing(ws, **values)


def test_faster_budget_remains_persistent_and_respects_every_rolling_window(paced):
    set_pacing(paced, verified_units_per_minute=6000, units_per_minute=1800, min_interval_seconds=0.5)
    settings = paced.config.gmail_pacing
    # Multiple minutes and a limiter restart; mixed listing, reads, and retries all count.
    for _ in range(2):
        limiter = QuotaLimiter(paced.config.store_dir, settings, "src_a", clock=paced.clock, sleep=paced.clock.sleep)
        try:
            for i in range(120):
                limiter.acquire("users.messages.list" if i % 10 == 0 else "users.messages.get")
        finally:
            limiter.close()
    rows = attempts(paced)
    assert len(rows) == 240
    assert all(b["at"] - a["at"] >= 0.5 for a, b in zip(rows, rows[1:], strict=False))
    for row in rows:
        assert sum(r["units"] for r in rows if row["at"] - 60 < r["at"] <= row["at"]) <= 1800


def test_faster_pacing_keeps_shared_daily_ceiling(paced):
    set_pacing(paced, verified_units_per_minute=6000, units_per_minute=1800,
               min_interval_seconds=0.667, daily_units=300)
    assert connect.sync(paced.config, "src_a")["termination"] == "daily-budget-stop"
    assert connect.sync(paced.config, "src_b")["termination"] == "daily-budget-stop"
    assert sum(r["units"] for r in attempts(paced)) <= 300


def test_progress_reports_counts_and_waits_without_identifying_data(paced):
    set_pacing(paced, units_per_minute=200)
    snaps = []
    tracker = Progress("src_a", snaps.append, clock=paced.clock, every_items=5)
    connect.sync(paced.config, "src_a", progress=tracker)
    events = {s["event"] for s in snaps}
    assert {"progress", "waiting", "finished", "phase"} <= events
    text = json.dumps(snaps)
    for native in paced.service.data["messages"]:
        assert native not in text
    assert "@" not in text and "://" not in text and "Subject" not in text
    rate = snaps[-1]["rate"]
    assert rate["per_minute"] is not None and rate["minutes_measured"] >= 3 and rate["low"] <= rate["high"]


def test_status_reports_runs_and_budget_without_identifiers(paced):
    connect.sync(paced.config, "src_a", max_items=4)
    report = connect.status(paced.config, "src_a")
    assert report["runs"][0]["termination"] == "capped" and report["full_sync_in_progress"]
    assert report["budget"]["day_used"] > 0 and report["budget"]["lock_held_by_another_command"] is False
    text = json.dumps(report)
    for native in paced.service.data["messages"]:
        assert native not in text
    assert '"20"' not in text and "pageToken" not in text


def test_cli_exit_codes_and_clean_messages(paced, monkeypatch):
    from towpath.cli import app

    set_pacing(paced, daily_units=200)
    runner = CliRunner()
    result = runner.invoke(app, ["connect", "sync", "src_a", "--quiet", "--config", str(paced.root / "towpath.toml")])
    assert result.exit_code == 3
    assert "Traceback" not in result.output and "://" not in result.output
    status = runner.invoke(app, ["connect", "status", "src_a", "--config", str(paced.root / "towpath.toml")])
    assert status.exit_code == 0 and "daily-budget-stop" in status.output


def test_existing_local_database_upgrades_in_place_and_resumes(paced):
    import sqlite3

    from towpath.stores import SCHEMAS

    path = paced.config.store_dir / "source.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    old = sqlite3.connect(path)  # the schema as released before this change
    old.executescript(SCHEMAS["source"].replace(
        "CREATE TABLE IF NOT EXISTS full_sync_listed (\n  source_id TEXT NOT NULL, native_id TEXT NOT NULL, "
        "PRIMARY KEY (source_id, native_id));", ""))
    old.execute("INSERT INTO sync_state (source_id, cursor, full_sync_history_id, full_sync_run) "
                "VALUES ('src_a', NULL, ?, 'run_0001')", (paced.service.data["historyId"],))
    old.execute("INSERT INTO runs (run_id, source_id, kind, started_at) VALUES ('run_0001', 'src_a', 'full', 'then')")
    old.commit()
    assert "termination" not in {r[1] for r in old.execute("PRAGMA table_info(runs)")}
    old.close()
    result = connect.sync(paced.config, "src_a")
    assert result["kind"] == "resumed-full" and result["complete"]
    db = paced.db("source")
    assert {"termination", "reason", "items_processed"} <= {r[1] for r in db.execute("PRAGMA table_info(runs)")}
    assert db.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 2
    db.close()


def test_on_demand_attachment_fetches_are_paced_and_accounted(paced):
    paced.full_pipeline()
    fetches = [r for r in attempts(paced) if r["method"] == "users.messages.attachments.get"]
    assert fetches and all(r["units"] == 20 for r in fetches)
    times = [r["at"] for r in attempts(paced)]
    assert all(b - a >= 1.0 for a, b in zip(times, times[1:], strict=False))
