"""Folders on the NAS from the Connections page: picking, Recoll configuration, background indexing with
progress and pause, and the paged import into search. A fake ``recollindex`` and the stub Recoll binding stand
in for Recoll; the folders themselves are invented and only ever read."""

import json
import os
import stat
import sys
import textwrap
import time
from contextlib import closing
from pathlib import Path

from django.test import Client, override_settings
import pytest

from towpath import config as config_mod, connections, folders, layout as layout_mod, request_worker
from towpath.stores import open_store
from towpath.unified import federation, sources
from towpath.unified.contracts import Filters
from towpath.web import auth
from towpath.web.application import configure

sys.path.insert(0, str(Path(__file__).parent))
from test_discovery_recoll import STUB, _doc  # noqa: E402

FAKE_RECOLLINDEX = """#!/bin/sh
# Stand-in for recollindex: writes Recoll-style progress, then waits as long as fake-duration says.
conf="$2"
printf 'phase = 1\\nfilesdone = 3\\ndocsdone = 3\\nfileerrors = 1\\ntotfiles = 4\\n' > "$conf/idxstatus.txt"
printf 'fn = /secret/name.txt\\n' >> "$conf/idxstatus.txt"
sleep "$(cat "$conf/fake-duration" 2>/dev/null || echo 0)"
printf 'phase = 5\\nfilesdone = 4\\ndocsdone = 4\\nfileerrors = 1\\n' > "$conf/idxstatus.txt"
printf 'dbtotdocs = 4\\ntotfiles = 4\\n' >> "$conf/idxstatus.txt"
exit "$(cat "$conf/fake-exit" 2>/dev/null || echo 0)"
"""


class Instance:
    def __init__(self, tmp_path: Path, monkeypatch):
        self.library = tmp_path / "library"
        for rel in ("photos/2003/raw", "documents/letters", "documents/taxes", "music"):
            (self.library / rel).mkdir(parents=True)
        (self.library / "photos/2003/raw/DSC_0001.NEF").write_bytes(b"raw")
        (self.library / "photos/2003/canal.jpg").write_bytes(b"jpeg")
        (self.library / "documents/letters/lock-keeper.txt").write_text("Letter to the lock keeper")
        (self.library / "documents/taxes/2003.pdf").write_bytes(b"%PDF")
        (self.library / "private").mkdir(mode=0o000)
        monkeypatch.setenv(folders.LIBRARY_ENV, str(self.library))
        stub = tmp_path / "stub"
        for rel, code in STUB.items():
            (stub / rel).parent.mkdir(parents=True, exist_ok=True)
            (stub / rel).write_text(textwrap.dedent(code))
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        (bin_dir / "recollindex").write_text(FAKE_RECOLLINDEX)
        (bin_dir / "recollindex").chmod(0o755)
        monkeypatch.setenv("PYTHONPATH", str(stub))
        monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
        self.data = tmp_path / "data"
        self.data.mkdir()
        self.layout = layout_mod.prepare(self.data)
        self.config = config_mod.for_data(self.layout)
        self.conf = self.data / "index" / "recoll"

    def recoll_holds(self, *rels: str, duration: float = 0, exit_code: int = 0):
        """What the fake Recoll index will contain once indexing has run."""
        self.conf.mkdir(parents=True, exist_ok=True)
        rows = [_doc(self.library, rel, "", "text/plain", "", Path(rel).name, "01073001600") for rel in rels]
        (self.conf / "fake-index.json").write_text(json.dumps(rows))
        (self.conf / "fake-duration").write_text(str(duration))
        (self.conf / "fake-exit").write_text(str(exit_code))

    def poll_until_idle(self, limit: float = 20):
        deadline = time.monotonic() + limit
        while time.monotonic() < deadline:
            request_worker.run_once(self.config, self.layout.credentials)
            if connections.get(self.config.store_dir, folders.SID)["indexing"] in ("idle", "paused"):
                return
            time.sleep(0.1)
        raise AssertionError("indexing did not finish")


@pytest.fixture
def inst(tmp_path, monkeypatch):
    yield Instance(tmp_path, monkeypatch)
    (tmp_path / "library" / "private").chmod(0o700)
    for proc in folders._running.values():
        folders._stop(proc)
    folders._running.clear()


def test_the_picker_shows_two_levels_and_marks_unreadable_folders(inst):
    tree = {t["name"]: t for t in folders.tree()}
    assert sorted(tree) == ["documents", "music", "photos", "private"]
    assert [c["name"] for c in tree["documents"]["children"]] == ["letters", "taxes"]
    if os.geteuid() != 0:
        assert tree["private"]["readable"] is False


def test_choosing_folders_validates_drops_nested_ones_and_grants_search(inst):
    lib = inst.library
    with pytest.raises(connections.ConnectionProblem, match="not a folder in the library"):
        folders.choose(inst.config, [str(lib.parent)])
    folders.choose(inst.config, [str(lib / "documents"), str(lib / "documents/letters"), str(lib / "photos")])
    c = connections.get(inst.config.store_dir, folders.SID)
    assert [(r["alias"], r["rel"]) for r in c["settings"]["roots"]] == [
        ("photos", "photos"), ("documents", "documents")]
    merged = connections.merged(inst.config, inst.layout.credentials)
    assert set(merged.files.roots) == {"photos", "documents"}
    from towpath.discovery import policy
    assert policy.granted(merged) == {"photos": {"search"}, "documents": {"search"}}
    folders.choose(inst.config, [str(lib / "documents"), str(lib / "music")])  # aliases are kept across changes
    roots = connections.get(inst.config.store_dir, folders.SID)["settings"]["roots"]
    assert [r["alias"] for r in roots] == ["music", "documents"]


def test_recoll_configuration_lists_everything_and_reads_media_by_name_only(inst, tmp_path):
    conf = folders.write_conf(tmp_path / "conf", [{"path": "/library/My Photos"}, {"path": "/library/docs"}],
                              tmp_path / "scratch")
    text = conf.read_text()
    assert 'topdirs = "/library/My Photos" "/library/docs"' in text
    assert ".nef" in text and ".mp4" in text and "followLinks = 0" in text
    # Recoll's own defaults skip caches, .git and tmp folders; replacing them keeps those findable.
    assert "skippedNames = .zfs .snapshot\n" in text and "skippedNames+" not in text
    # Without this, Recoll opens every unrecognised file to guess its type, then lists it by name anyway.
    assert "usesystemfilecommand = 0" in text


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
    time.sleep(0.3)
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


def test_a_recoll_failure_is_reported_and_can_be_retried(inst):
    folders.choose(inst.config, [str(inst.library / "documents")])
    inst.recoll_holds("documents/letters/lock-keeper.txt", exit_code=3)
    connections.start_indexing(inst.config.store_dir, inst.layout.credentials, folders.SID)
    inst.poll_until_idle()
    c = connections.get(inst.config.store_dir, folders.SID)
    assert c["indexing"] == "idle" and "exit 3" in c["last_error"]


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
        connections._update(inst.config.store_dir, folders.SID, progress={"phase": "done", "indexed": 1234})
        assert "1,234 files in search" in client.get("/connections/").content.decode()
        detail = client.get("/connections/folders/").content.decode()
        assert "Files in search" in detail and "documents" in detail and ">Pause<" in detail
        assert client.post("/connections/add/folders", {"folder": ["/etc"]}).status_code == 400


def test_mail_only_instances_never_load_file_discovery(tmp_path):
    import subprocess
    code = ("import sys; from pathlib import Path; from towpath import config, connections, layout; "
            f"l = layout.prepare(Path({str(tmp_path)!r})); connections.merged(config.for_data(l), l.credentials); "
            "print(any(m.startswith('towpath.discovery') for m in sys.modules))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"
