"""Upgrading stores created by the base revision (3b13228): explicit, idempotent, by writer role, lossless."""

import hashlib
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

import pytest
from django.test import Client, override_settings
from typer.testing import CliRunner

from towpath import stores
from towpath.cli import app
from towpath.web.application import configure

sys.path.insert(0, str(Path(__file__).parent))
from legacy_stores_3b13228 import DDL  # noqa: E402

MIDDLEWARE = ["towpath.web.views.LocalPrivacyMiddleware", "towpath.web.views.StoreSchemaMiddleware",
              "django.middleware.csrf.CsrfViewMiddleware"]

ROWS = {
    "source": [
        "INSERT INTO sources VALUES ('gmail-old','mail-provider','gmail','{}')",
        "INSERT INTO runs (run_id, source_id, kind, started_at, finished_at, complete, termination) VALUES "
        "('run_0001','gmail-old','full','2026-09-01T00:00:00+00:00','2026-09-02T00:00:00+00:00',1,'complete')",
        "INSERT INTO items (item_id, source_id, native_id, subject, from_addr, date_utc, internal_date, first_seen_run,"
        " last_seen_run) VALUES ('itm_1','gmail-old','m1','Canal society minutes','clerk@example.org',"
        "'2009-04-02T10:15:00+00:00','1238667300000','run_0001','run_0001')",
        "INSERT INTO parts (item_id, part_id, depth, mime_type, filename, size) VALUES "
        "('itm_1','1',1,'image/x-nikon-nef','DSC_0001.NEF',4096)",
        "INSERT INTO observations VALUES ('itm_1','run_0001','2026-09-02T00:00:00+00:00','[\"INBOX\"]')",
        "INSERT INTO cache VALUES ('abc', X'00010203', '2026-09-02T00:00:00+00:00')",
        "INSERT INTO coverage (run_id, source_id, complete, items_seen, items_new, items_absent, items_indexed_total,"
        " unparseable, termination) VALUES ('run_0001','gmail-old',1,1,1,0,1,'[]','complete')",
    ],
    "queue": [
        "INSERT INTO content_requests (item_id, part_id, requested_by, priority, created_at) VALUES "
        "('itm_1','1','scan_1','background','2026-09-03T00:00:00+00:00')",
    ],
    "decisions": [
        "INSERT INTO settings VALUES ('itm_1','model_use','excluded','owner','2026-09-04T00:00:00+00:00')",
        "INSERT INTO file_grants VALUES ('archive','search','owner','2026-09-04T00:00:00+00:00')",
        "INSERT INTO model_grants VALUES ('local','fp1','metadata','owner','2026-09-04T00:00:00+00:00')",
        "INSERT INTO dismissals VALUES ('mk1','prp_1','not needed','owner','2026-09-04T00:00:00+00:00')",
        "INSERT INTO decision_log (kind, target_id, detail, author, at) VALUES "
        "('setting','itm_1','{\"model_use\": \"excluded\"}','owner','2026-09-04T00:00:00+00:00')",
    ],
}


@pytest.fixture
def legacy(tmp_path):
    """Stores exactly as the base revision created them, holding invented content and owner approvals."""
    store_dir = tmp_path / "state"
    store_dir.mkdir()
    for store, statements in DDL.items():
        with closing(sqlite3.connect(store_dir / f"{store}.db")) as db:
            for statement in statements:
                db.execute(statement)
            for row in ROWS[store]:
                db.execute(row)
            db.commit()
    return store_dir


def dump(path: Path) -> dict:
    with closing(sqlite3.connect(path)) as db:
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' "
                                           "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        return {t: sorted(map(repr, db.execute(f"SELECT * FROM {t}").fetchall())) for t in tables}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_status_names_every_missing_table_without_writing(legacy):
    before = {s: digest(legacy / f"{s}.db") for s in DDL}
    report = {r["store"]: r for r in stores.schema_status(legacy)}
    assert report["source"]["missing_tables"] == ["search_runs"]
    assert report["queue"]["missing_tables"] == ["search_requests"]
    assert report["decisions"]["missing_tables"] == ["collection_items", "collections", "source_grants"]
    assert all(not r["current"] for s, r in report.items() if s in DDL)
    assert report["files"]["exists"] is False and report["files"]["current"] is True
    assert {s: digest(legacy / f"{s}.db") for s in DDL} == before


def test_upgrade_adds_only_new_tables_preserves_content_and_repeats_as_a_no_op(legacy):
    before = {s: dump(legacy / f"{s}.db") for s in DDL}
    first = {r["store"]: r for r in stores.upgrade(legacy)}
    assert first["source"]["added_tables"] == ["search_runs"] and first["source"]["writer_role"] == "connect"
    assert first["queue"]["added_tables"] == ["search_requests"] and first["queue"]["writer_role"] == "web"
    assert first["decisions"]["writer_role"] == "web"
    for store, tables in before.items():
        after = dump(legacy / f"{store}.db")
        assert {t: after[t] for t in tables} == tables  # every old row, unchanged
        assert all(after[t] == [] for t in set(after) - set(tables))  # new tables start empty
    assert all(r["current"] for r in stores.schema_status(legacy))
    snapshot = {s: dump(legacy / f"{s}.db") for s in DDL}
    second = stores.upgrade(legacy)
    assert all(r["added_tables"] == [] and r["added_columns"] == {} for r in second)
    assert {s: dump(legacy / f"{s}.db") for s in DDL} == snapshot
    assert not (legacy / "files.db").exists() and not (legacy / "derived.db").exists()  # nothing invented


def test_upgrade_opens_each_store_only_with_its_writer_role(legacy, monkeypatch):
    seen = []
    real = stores.open_store

    def spy(store_dir, store, role):
        seen.append((store, role))
        return real(store_dir, store, role)

    monkeypatch.setattr(stores, "open_store", spy)
    stores.upgrade(legacy)
    writes = [(s, r) for s, r in seen if r in stores.WRITERS[s]]
    assert sorted(writes) == [("decisions", "web"), ("queue", "web"), ("source", "connect")]


def test_the_ui_refuses_outdated_stores_with_an_actionable_message_and_never_migrates_source(legacy):
    source_before = digest(legacy / "source.db")
    out = CliRunner().invoke(app, ["web", "serve", "--store-dir", str(legacy)])
    assert out.exit_code == 2 and "towpath stores upgrade" in out.output
    configure(legacy, None)
    with override_settings(TOWPATH_STORE_DIR=legacy, TOWPATH_CONFIG=None, ALLOWED_HOSTS=["testserver"],
                           TOWPATH_LOGIN_REQUIRED=False, MIDDLEWARE=MIDDLEWARE):
        client = Client()
        for path in ("/search/?q=canal", "/collections/"):
            response = client.get(path)
            assert response.status_code == 503
            assert b"towpath stores upgrade" in response.content and b"no such table" not in response.content
        assert client.get("/email/").status_code == 200  # pages that need no new table keep working
    assert digest(legacy / "source.db") == source_before


def test_after_the_upgrade_the_ui_works_and_old_decisions_still_apply(legacy):
    stores.upgrade(legacy)
    configure(legacy, None)
    with override_settings(TOWPATH_STORE_DIR=legacy, TOWPATH_CONFIG=None, ALLOWED_HOSTS=["testserver"],
                           TOWPATH_LOGIN_REQUIRED=False, MIDDLEWARE=MIDDLEWARE):
        client = Client()
        page = client.get("/search/", {"q": "extension:nef"})
        assert page.status_code == 200 and b"DSC_0001.NEF" in page.content
        assert client.get("/collections/").status_code == 200
    from towpath.unified import grants

    assert grants.item_settings(legacy, "itm_1") == {"model_use": "excluded"}


def test_store_commands_and_search_preflight(legacy, tmp_path):
    runner = CliRunner()
    config = tmp_path / "towpath.toml"
    config.write_text('[stores]\ndir = "state"\n')
    blocked = runner.invoke(app, ["search", "sources", "--config", str(config), "--local"])
    assert blocked.exit_code == 2
    assert json.loads(blocked.output)["error"]["code"] == "store-upgrade-needed"
    status = runner.invoke(app, ["stores", "status", "--store-dir", str(legacy)])
    assert status.exit_code == 1 and not json.loads(status.output)["current"]
    upgraded = runner.invoke(app, ["stores", "upgrade", "--store-dir", str(legacy)])
    assert upgraded.exit_code == 0, upgraded.output
    assert {r["store"] for r in json.loads(upgraded.output)["stores"] if r["added_tables"]} == \
        {"source", "queue", "decisions"}
    assert runner.invoke(app, ["stores", "status", "--store-dir", str(legacy)]).exit_code == 0
    assert runner.invoke(app, ["search", "sources", "--config", str(config), "--local"]).exit_code == 0
    only = runner.invoke(app, ["stores", "upgrade", "--store-dir", str(legacy), "--store", "nope"])
    assert only.exit_code == 2
