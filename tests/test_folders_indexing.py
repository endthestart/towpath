"""The Folders connection: indexing in the background, adding to search, pausing, failures, and the page."""

import json
import os
import sqlite3
import stat
import time
from contextlib import closing

from django.test import Client, override_settings
import pytest

from towpath import connections, folders, request_worker
from towpath.stores import open_store
from towpath.unified import federation, sources
from towpath.unified.contracts import Filters
from towpath.web import auth
from towpath.web.application import configure


def test_progress_shows_what_search_holds_and_a_measured_pace(monkeypatch):
    clock = iter(["2026-10-08T12:00:00+00:00", "2026-10-08T12:00:10+00:00", "2026-10-08T12:01:00+00:00",
                  "2026-10-08T12:02:00+00:00", "2026-10-08T12:03:00+00:00"])
    monkeypatch.setattr(folders, "_now", lambda: next(clock))
    status = {"filesdone": 1000, "dbtotdocs": 4_000_000}  # Recoll's entries, folders and members included
    first = folders._progress(status, "reading", previous=folders._carried({"progress": {"in_search": 250}}))
    assert (first["indexed"], first["rate"]) == (250, None)  # never Recoll's own count
    early = folders._progress({**status, "filesdone": 1100}, "reading", previous=first)
    assert early["rate"] is None and early["since"] == first["since"]  # too soon to measure
    measured = folders._progress({**status, "filesdone": 1600}, "reading", previous=early)
    assert measured["rate"] == {"per_minute": 600} and measured["indexed"] == 250
    smoothed = folders._progress({**status, "filesdone": 1800}, "reading", previous=measured)
    assert smoothed["rate"] == {"per_minute": 400}  # half the new minute's 200, half the last 600
    assert folders._progress(status, "reading")["indexed"] is None  # nothing added yet


def test_status_keeps_counts_but_never_the_current_file_name(tmp_path):
    (tmp_path / "idxstatus.txt").write_text("phase = 1\nfn = /secret/name.txt\nfilesdone = 12\ndbtotdocs = 9\n")
    assert folders.read_status(tmp_path) == {"phase": 1, "filesdone": 12, "dbtotdocs": 9}


def test_indexing_runs_in_the_background_then_files_become_searchable(inst, monkeypatch):
    monkeypatch.setattr(folders, "PAGE", 2)  # several pages for four files
    lib = inst.library
    folders.choose(inst.config, [str(lib / "documents"), str(lib / "photos")])
    inst.recoll_holds("documents/letters/lock-keeper.txt", "documents/taxes/2003.pdf", "photos/2003/canal.jpg",
                      "photos/2003/raw/DSC_0001.NEF", duration=1)
    connections.start_indexing(inst.config.store_dir, inst.layout.credentials, folders.SID)
    assert request_worker.run_once(inst.config, inst.layout.credentials)["indexing"][0]["step"] == "started"
    deadline = time.monotonic() + 10
    while "filesdone" not in folders.read_status(inst.conf) and time.monotonic() < deadline:
        time.sleep(0.05)
    request_worker.run_once(inst.config, inst.layout.credentials)  # while Recoll runs: its counts, not its files
    progress = connections.get(inst.config.store_dir, folders.SID)["progress"]
    assert (progress["phase"], progress["files"], progress["total"]) == ("reading", 3, 4)
    assert "secret" not in json.dumps(progress)
    inst.poll_until_idle()
    c = connections.get(inst.config.store_dir, folders.SID)
    assert c["progress"]["indexed"] == 4 and c["last_error"] is None and c["progress"]["phase"] == "done"
    web = connections.merged(inst.config, None, role="web")
    adapters = sources.build_adapters(web, connect=False)
    found = federation.search(adapters, Filters.parse("canal"), 20).to_dict()
    assert [r["title"] for r in found["results"]] == ["canal.jpg"]
    nef = federation.search(adapters, Filters.parse("extension:nef"), 20).to_dict()
    assert [r["source_id"] for r in nef["results"]] == ["files-folders"]
    for path in (lib / "documents/letters/lock-keeper.txt", lib / "photos/2003/canal.jpg"):
        assert stat.S_IMODE(path.stat().st_mode) & 0o222  # still the owner's files, untouched
    with closing(open_store(inst.config.store_dir, "files", "web")) as db:
        runs = db.execute("SELECT termination FROM runs WHERE kind = 'import'").fetchall()
    assert [r[0] for r in runs] == ["complete", "complete"]


def test_pause_stops_recoll_and_resume_continues(inst):
    folders.choose(inst.config, [str(inst.library / "documents")])
    inst.recoll_holds("documents/letters/lock-keeper.txt", duration=30)
    connections.start_indexing(inst.config.store_dir, inst.layout.credentials, folders.SID)
    request_worker.run_once(inst.config, inst.layout.credentials)
    proc = folders._running[folders.SID]
    connections.pause_indexing(inst.config.store_dir, folders.SID)
    assert request_worker.run_once(inst.config, inst.layout.credentials)["indexing"] == [
        {"source_id": "folders", "step": "paused"}]
    assert proc.poll() is not None and folders.SID not in folders._running
    (inst.conf / "fake-duration").write_text("0")
    connections.start_indexing(inst.config.store_dir, inst.layout.credentials, folders.SID)
    inst.poll_until_idle()
    assert connections.get(inst.config.store_dir, folders.SID)["progress"]["indexed"] == 1


def test_adding_to_search_runs_beside_the_worker_loop(inst, monkeypatch):
    import threading

    folders.choose(inst.config, [str(inst.library / "documents")])
    inst.recoll_holds("documents/letters/lock-keeper.txt")
    release, real = threading.Event(), folders._import
    monkeypatch.setattr(folders, "_import", lambda *a: (release.wait(10), real(*a))[1])
    connections.start_indexing(inst.config.store_dir, inst.layout.credentials, folders.SID)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        steps = request_worker.run_once(inst.config, inst.layout.credentials)["indexing"]
        if steps and steps[0]["step"] == "adding":
            break
        time.sleep(0.05)
    started = time.monotonic()
    assert request_worker.run_once(inst.config, inst.layout.credentials)["indexing"][0]["step"] == "adding"
    assert time.monotonic() - started < 2  # the poll returns while the import waits
    release.set()
    inst.poll_until_idle()
    c = connections.get(inst.config.store_dir, folders.SID)
    assert c["progress"]["indexed"] == 1 and c["progress"]["phase"] == "done"


def test_a_restart_while_adding_carries_on_adding_without_reading_again(inst, monkeypatch):
    folders.choose(inst.config, [str(inst.library / "documents")])
    inst.recoll_holds("documents/letters/lock-keeper.txt")
    connections.start_indexing(inst.config.store_dir, inst.layout.credentials, folders.SID)
    inst.poll_until_idle()
    connections._update(inst.config.store_dir, folders.SID, indexing="running",  # as a restart leaves it
                        progress=folders._progress(folders.read_status(inst.conf), "adding", 0))
    monkeypatch.setattr(folders, "_start", lambda *a: pytest.fail("Recoll started again"))
    assert request_worker.run_once(inst.config, inst.layout.credentials)["indexing"][0]["step"] == "adding"
    inst.poll_until_idle()
    c = connections.get(inst.config.store_dir, folders.SID)
    assert c["indexing"] == "idle" and c["progress"]["phase"] == "done" and c["progress"]["indexed"] == 1


def test_a_failure_while_adding_is_reported(inst, monkeypatch):
    folders.choose(inst.config, [str(inst.library / "documents")])
    inst.recoll_holds("documents/letters/lock-keeper.txt")
    monkeypatch.setattr(folders, "_import", lambda *a: 1 / 0)
    connections.start_indexing(inst.config.store_dir, inst.layout.credentials, folders.SID)
    inst.poll_until_idle()
    c = connections.get(inst.config.store_dir, folders.SID)
    assert c["indexing"] == "idle" and "Adding files to search stopped" in c["last_error"]
    with closing(sqlite3.connect(inst.config.store_dir / "connections.db")) as db:
        detail = json.loads(db.execute("SELECT detail FROM connection_events WHERE event = 'indexing-stopped'")
                            .fetchone()[0])
    assert detail["error"] == "ZeroDivisionError"
    assert detail["at"].startswith("test_folders_indexing.py:")  # code, not files


def test_continuing_after_a_failure_while_adding_skips_reading_again(inst, monkeypatch):
    folders.choose(inst.config, [str(inst.library / "documents")])
    inst.recoll_holds("documents/letters/lock-keeper.txt")
    real = folders._import
    monkeypatch.setattr(folders, "_import", lambda *a: 1 / 0)
    connections.start_indexing(inst.config.store_dir, inst.layout.credentials, folders.SID)
    inst.poll_until_idle()
    assert "Continue to try again" in connections.get(inst.config.store_dir, folders.SID)["last_error"]
    monkeypatch.setattr(folders, "_import", real)
    monkeypatch.setattr(folders, "_start", lambda *a: pytest.fail("Recoll started again"))
    connections.start_indexing(inst.config.store_dir, inst.layout.credentials, folders.SID)
    assert request_worker.run_once(inst.config, inst.layout.credentials)["indexing"][0]["step"] == "adding"
    inst.poll_until_idle()
    c = connections.get(inst.config.store_dir, folders.SID)
    assert (c["indexing"], c["last_error"], c["progress"]["phase"]) == ("idle", None, "done")
    assert c["progress"]["indexed"] == 1


def test_a_recoll_failure_is_reported_and_can_be_retried(inst):
    folders.choose(inst.config, [str(inst.library / "documents")])
    inst.recoll_holds("documents/letters/lock-keeper.txt", exit_code=3)
    connections.start_indexing(inst.config.store_dir, inst.layout.credentials, folders.SID)
    inst.poll_until_idle()
    c = connections.get(inst.config.store_dir, folders.SID)
    assert c["indexing"] == "idle" and "exit 3" in c["last_error"]


def test_indexing_can_be_started_and_paused_from_the_command_line(inst):
    from typer.testing import CliRunner

    from towpath.cli import app

    folders.choose(inst.config, [str(inst.library / "documents")])
    run = lambda *a: CliRunner().invoke(app, ["connect", "indexing", *a, "--data-dir", str(inst.data)])  # noqa: E731
    assert run("folders", "start").output.strip() == "folders: requested"
    assert run("folders", "pause").output.strip() == "folders: paused"
    assert run("folders", "stop").exit_code != 0 and run("nothing", "start").exit_code == 1


def test_nothing_runs_until_the_owner_starts_it(inst):
    folders.choose(inst.config, [str(inst.library / "documents")])
    assert request_worker.run_once(inst.config, inst.layout.credentials)["indexing"] == []
    assert folders.SID not in folders._running


def test_the_folders_page_picks_and_starts(inst, monkeypatch):
    monkeypatch.setattr(auth, "FAIL_DELAY_SECONDS", 0)
    configure(inst.config.store_dir)
    auth.create_account(inst.config.store_dir, "owner", "correct horse battery staple")
    with override_settings(TOWPATH_STORE_DIR=inst.config.store_dir, TOWPATH_CONFIG=inst.config,
                           ALLOWED_HOSTS=["testserver"], ROOT_URLCONF="towpath.web.connection_urls",
                           TOWPATH_CREDENTIALS_DIR=inst.layout.credentials):
        client = Client()
        client.post("/login", {"username": "owner", "password": "correct horse battery staple"})
        assert "Folders on this server" in client.get("/connections/").content.decode()
        page = client.get("/connections/add/folders").content.decode()
        assert "letters" in page and "Towpath can't read this folder yet" in page or os.geteuid() == 0
        response = client.post("/connections/add/folders", {"folder": [str(inst.library / "documents")],
                                                            "start": "1"})
        assert response["Location"] == "/connections/folders/"
        connections._update(inst.config.store_dir, folders.SID, progress={"phase": "done", "indexed": 1234,
                                                                             "in_search": 1234})
        assert "1,234 items in search" in client.get("/connections/").content.decode()
        detail = client.get("/connections/folders/").content.decode()
        assert "In search" in detail and "documents" in detail and ">Pause<" in detail
        assert client.post("/connections/add/folders", {"folder": ["/etc"]}).status_code == 400
