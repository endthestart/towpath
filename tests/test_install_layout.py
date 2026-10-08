"""A shell-free install: one data folder, created layout, optional settings file, carried-over settings."""

import os
import stat

from django.test import Client, override_settings
import pytest
from typer.testing import CliRunner

from towpath import config as config_mod, connections, layout as layout_mod
from towpath.cli import app
from towpath.quota import PacingSettings
from towpath.web import auth
from towpath.web.application import configure


def test_first_start_creates_the_layout_and_later_starts_change_nothing(tmp_path):
    data = tmp_path / "towpath"
    data.mkdir()
    layout = layout_mod.prepare(data)
    assert sorted(p.name for p in data.iterdir()) == ["credentials", "index", "scratch", "state"]
    assert stat.S_IMODE(layout.credentials.stat().st_mode) == 0o700
    (layout.state / "source.db").write_bytes(b"kept")
    layout_mod.prepare(data)
    assert (layout.state / "source.db").read_bytes() == b"kept"
    assert layout.config is None


def test_an_earlier_tokens_folder_becomes_credentials_with_its_files(tmp_path):
    (tmp_path / "tokens").mkdir(mode=0o750)
    (tmp_path / "tokens" / "gmail-token.json").write_text("{}")
    layout = layout_mod.prepare(tmp_path)
    assert not (tmp_path / "tokens").exists() and (layout.credentials / "gmail-token.json").read_text() == "{}"
    assert stat.S_IMODE(layout.credentials.stat().st_mode) == 0o700


@pytest.mark.skipif(os.geteuid() == 0, reason="root can write anywhere")
def test_an_unwritable_folder_is_explained(tmp_path):
    tmp_path.chmod(0o500)
    try:
        with pytest.raises(layout_mod.LayoutError, match="Edit Permissions"):
            layout_mod.prepare(tmp_path)
    finally:
        tmp_path.chmod(0o700)
    with pytest.raises(layout_mod.LayoutError, match="missing"):
        layout_mod.prepare(tmp_path / "absent")
    with pytest.raises(layout_mod.LayoutError):
        layout_mod.state_only(tmp_path)  # the web service needs state/ to exist already


def test_the_settings_file_is_optional_and_never_moves_the_stores(tmp_path):
    layout = layout_mod.prepare(tmp_path)
    default = config_mod.for_data(layout)
    assert default.store_dir == layout.state.resolve() and default.sources == {}
    assert default.gmail_pacing == PacingSettings()
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "towpath.toml").write_text('[stores]\ndir = "/somewhere/else"\n[gmail_pacing]\n'
                                                      'units_per_minute = 600\n')
    legacy = config_mod.for_data(layout)  # an instance set up before the data folder kept it in config/
    assert legacy.store_dir == layout.state.resolve() and legacy.gmail_pacing.units_per_minute == 600
    (tmp_path / "towpath.toml").write_text('[gmail_pacing]\nunits_per_minute = 300\n')
    assert config_mod.for_data(layout).gmail_pacing.units_per_minute == 300


def test_pacing_from_a_settings_file_is_carried_over_once(tmp_path):
    layout = layout_mod.prepare(tmp_path)
    (tmp_path / "towpath.toml").write_text('[gmail_pacing]\nunits_per_minute = 1800\nmin_interval_seconds = 0.667\n'
                                           'verified_units_per_minute = 6000\n')
    config = config_mod.for_data(layout)
    connections.import_configured(config, layout.credentials)
    (tmp_path / "towpath.toml").unlink()
    bare = config_mod.for_data(layout)
    assert connections.gmail_pacing(bare).units_per_minute == 1800  # survives losing the file
    connections.set_verified_quota(bare, 2000)
    connections.import_configured(config, layout.credentials)  # never overwrites a choice made on the page
    assert connections.gmail_pacing(bare).units_per_minute == 600


def test_the_connector_prepares_a_fresh_folder_and_explains_a_bad_one(tmp_path):
    data = tmp_path / "towpath"
    data.mkdir()
    result = CliRunner().invoke(app, ["connect", "run-worker", "--data-dir", str(data), "--once"])
    assert result.exit_code == 0, result.output
    assert (data / "credentials").is_dir() and (data / "state").is_dir()
    missing = CliRunner().invoke(app, ["connect", "run-worker", "--data-dir", str(tmp_path / "nope"), "--once"])
    assert missing.exit_code == 2 and "Create it and restart" in missing.output


def test_the_connector_brings_its_stores_up_to_date_before_the_web_service_reads_them(tmp_path):
    import sqlite3

    from towpath import stores

    layout = layout_mod.prepare(tmp_path)
    old = sqlite3.connect(layout.state / "files.db")  # a catalog from before the counts table
    old.executescript(stores.SCHEMAS["files"].replace("CREATE TABLE IF NOT EXISTS catalog_counts", "CREATE TABLE x"))
    old.close()
    assert not stores.schema_status(layout.state, ["files"])[0]["current"]
    result = CliRunner().invoke(app, ["connect", "run-worker", "--data-dir", str(tmp_path), "--once"])
    assert result.exit_code == 0, result.output
    assert stores.schema_status(layout.state, ["files"])[0]["current"]


def test_the_web_service_starts_on_a_fresh_install(tmp_path, monkeypatch):
    layout = layout_mod.prepare(tmp_path)
    launched = {}
    monkeypatch.setattr("towpath.web.application.launch", lambda *a, **k: launched.update(args=a))
    result = CliRunner().invoke(app, ["web", "serve", "--data-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    store_dir, _, config = launched["args"][:3]
    assert store_dir == layout.state and config.sources == {}
    configure(layout.state)
    auth.create_account(layout.state, "owner", "correct horse battery staple")
    with override_settings(TOWPATH_STORE_DIR=layout.state, TOWPATH_CONFIG=config, ALLOWED_HOSTS=["testserver"]):
        client = Client()
        client.post("/login", {"username": "owner", "password": "correct horse battery staple"})
        for path in ("/", "/email/", "/search/", "/search/?q=canal", "/collections/", "/connections/"):
            assert client.get(path).status_code == 200, path
