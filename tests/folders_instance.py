"""A library of invented files, a stub of Recoll's binding and a fake recollindex, for the Folders tests."""

import json
import os
import textwrap
import time
from pathlib import Path

from towpath import config as config_mod, connections, folders, layout as layout_mod, request_worker
from tests.recoll_stub import STUB, _doc

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

    def poll_until_measured(self, limit: float = 20):
        """Poll until no background job runs and nothing is left to measure."""
        deadline = time.monotonic() + limit
        while time.monotonic() < deadline:
            request_worker.run_once(self.config, self.layout.credentials)
            c = connections.get(self.config.store_dir, folders.SID)
            if not folders._jobs and not folders._unmeasured(self.config, c):
                return
            time.sleep(0.05)
        raise AssertionError("measuring did not finish")

    def poll_until_idle(self, limit: float = 20):
        deadline = time.monotonic() + limit
        while time.monotonic() < deadline:
            request_worker.run_once(self.config, self.layout.credentials)
            if connections.get(self.config.store_dir, folders.SID)["indexing"] in ("idle", "paused"):
                return
            time.sleep(0.1)
        raise AssertionError("indexing did not finish")
