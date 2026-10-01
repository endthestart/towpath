import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from towpath import config as config_mod
from towpath import connect, scan
from towpath.fixtures import generator

REPO = Path(__file__).resolve().parents[1]


class Workspace:
    def __init__(self, root: Path):
        self.root = root
        self.summary = generator.generate(root)
        self.config = config_mod.load(root / "towpath.toml")

    def sync_all(self, **options):
        return {sid: connect.sync(self.config, sid, **(options if sid.startswith("src_") else {}))
                for sid in self.config.sources}

    def scan_all(self):
        return {sid: scan.run_scan(self.config, sid) for sid in self.config.selectors}

    def full_pipeline(self):
        self.sync_all()
        self.scan_all()
        connect.fetch_requests(self.config)
        return self.scan_all()

    def advance(self):
        self.summary = generator.advance(self.root)
        return self.summary

    def db(self, store: str, role: str = "web"):
        from towpath.stores import open_store
        return open_store(self.config.store_dir, store, role)

    def item(self, native_id: str, source_id: str = "src_a") -> str:
        return connect.item_id(source_id, native_id)

    def cli(self, *args: str) -> subprocess.CompletedProcess:
        env = dict(os.environ, PYTHONPATH=str(REPO / "src"))
        return subprocess.run([sys.executable, "-m", "towpath", *args, "--config", str(self.root / "towpath.toml")],
                              capture_output=True, text=True, env=env, cwd=self.root, check=True)

    def cli_json(self, *args: str):
        return json.loads(self.cli(*args, "--json").stdout)


@pytest.fixture
def ws(tmp_path):
    return Workspace(tmp_path)
