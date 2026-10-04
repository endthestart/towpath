import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from towpath import config as config_mod
from towpath import connect, scan
from towpath.discovery import corpus, policy, service
from towpath.fixtures import generator
from towpath.stores import open_store

REPO = Path(__file__).resolve().parents[1]

# Image tests run only against a built candidate image: TOWPATH_IMAGE=<image> pytest tests/container
collect_ignore_glob = [] if os.environ.get("TOWPATH_IMAGE") else ["container/*"]


class Files:
    """A synthetic file discovery corpus with the fixture provider and its example config."""

    def __init__(self, root: Path):
        self.root = root
        self.summary = corpus.generate(root)
        self.path = corpus.write_example_config(root)
        self.config = config_mod.load(self.path)

    def reload(self, old: str | None = None, new: str | None = None):
        if old is not None:
            self.path.write_text(self.path.read_text().replace(old, new))
        self.config = config_mod.load(self.path)
        return self.config

    def grant(self, root: str, *features: str):
        for feature in features:
            policy.grant(self.config, root, feature)

    def search(self, query: str, **kw):
        return service.search(self.config, query, **kw)

    def find(self, query: str, location_suffix: str) -> dict:
        for result in self.search(query)["results"]:
            if result["location"].endswith(location_suffix):
                return result
        raise AssertionError(f"no result ending {location_suffix!r}")

    def cli(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        env = dict(os.environ, PYTHONPATH=str(REPO / "src"))
        return subprocess.run([sys.executable, "-m", "towpath", *args, "--config", str(self.path)],
                              capture_output=True, text=True, env=env, cwd=self.root, check=check)

    def decision_log(self) -> list:
        db = open_store(self.config.store_dir, "decisions", "connect")
        return [tuple(r) for r in db.execute("SELECT kind, target_id, detail FROM decision_log")]


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

    def add_config(self, text: str):
        path = self.root / "towpath.toml"
        path.write_text(path.read_text() + "\n" + text)
        self.config = config_mod.load(path)
        return self.config

    def replace_config(self, old: str, new: str):
        path = self.root / "towpath.toml"
        path.write_text(path.read_text().replace(old, new))
        self.config = config_mod.load(path)
        return self.config

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


@pytest.fixture
def openai_stub():
    from tests.stubs import OpenAIStub
    stubs = []

    def make(**kwargs):
        stub = OpenAIStub(**kwargs)
        stubs.append(stub)
        return stub

    yield make
    for stub in stubs:
        stub.close()


@pytest.fixture
def fx(tmp_path):
    """Synthetic file discovery corpus with the fixture provider."""
    return Files(tmp_path)
