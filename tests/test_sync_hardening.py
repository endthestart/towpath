"""Gmail sync hardening (docs/evaluations/gmail-sync-hardening.md), on synthetic data only: pacing and retries.

A fake clock replaces real time, and a fake of Google's discovery client
returns Google-shaped responses and errors. Nothing here contacts Google.
"""

import fcntl
import os
from datetime import datetime, timezone

import pytest

from tests.gmail_pacing import CALL_METHOD, attempts, fail_get, http_error, run_row, set_pacing, target
from towpath import connect
from towpath.adapters.errors import LockBusy
from towpath.progress import Progress
from towpath.quota import METHOD_COSTS, BudgetLock


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
