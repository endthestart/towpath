"""A synthetic unified environment for tests: Gmail fixture, synthetic IMAP server, files corpus, manifest."""

import sys
from pathlib import Path

from towpath import config as config_mod
from towpath import connect
from towpath.discovery import policy, service
from towpath.unified import environment, federation, sources
from towpath.unified.contracts import Filters

sys.path.insert(0, str(Path(__file__).parent))
import imap_stub as stub  # noqa: E402


class Uni:
    def __init__(self, root: Path, monkeypatch):
        self.state = stub.State(stub.build(environment.imap_mailboxes()))
        self.server = stub.Server(self.state).__enter__()
        monkeypatch.setenv(environment.IMAP_PASSWORD_ENV, self.state.password)
        self.root = root
        environment.generate(root, imap_port=self.server.port)
        self.path = root / "towpath.toml"
        self.config = config_mod.load(self.path)
        for source_id in ("gmail-fixture", "imap-fixture"):
            assert connect.sync(self.config, source_id)["termination"] == "complete"
        for root_alias in ("archive", "shared", "photos"):
            policy.grant(self.config, root_alias, "search")
        service.import_catalog(self.config, "fixture")
        service.import_catalog(self.config, "inventory")

    def adapters(self, connect_mode: bool = True):
        return sources.build_adapters(self.config, connect=connect_mode)

    def search(self, query: str, connect_mode: bool = True, limit: int = 20, cursor=None) -> dict:
        adapters = self.adapters(connect_mode)
        try:
            return federation.search(adapters, Filters.parse(query), limit, cursor).to_dict()
        finally:
            federation.close_all(adapters)

    def close(self):
        self.server.__exit__(None, None, None)
