"""Gmail sync hardening: pacing settings, progress, status, the command line, and upgrades."""

import json

import pytest

from typer.testing import CliRunner

from tests.gmail_pacing import attempts, set_pacing
from towpath import config as config_mod
from towpath import connect
from towpath.adapters import build_connector
from towpath.progress import Progress
from towpath.quota import BudgetLock, PacingSettings, QuotaLimiter


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
