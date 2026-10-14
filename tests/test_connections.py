"""Accounts added in the UI: test, store, choose folders, index in slices, pause, disconnect.

Runs against the synthetic read-only IMAP server; every scenario also checks that no command could have
changed the mailbox and that the password never reaches a store, an event or the web role's view.
"""

from contextlib import closing
from pathlib import Path
import stat
import sys

import pytest

from towpath import config as config_mod, connections, request_worker
from towpath.stores import open_store

sys.path.insert(0, str(Path(__file__).parent))
import imap_stub as stub  # noqa: E402
from test_imap import WRITES  # noqa: E402

PASSWORD = "synthetic-app-password"


def mailboxes():
    msgs = {i: stub.Message(stub.message(i, f"Lock report {i}", f"Count {i} of the narrowboats."))
            for i in range(1, 8)}
    return {
        "INBOX": stub.Mailbox(1700000001, msgs),
        "Archive": stub.Mailbox(1700000002, {1: stub.Message(stub.message(20, "Old towpath map", "Map attached."))}),
        "Trash": stub.Mailbox(1700000003, {1: stub.Message(stub.message(30, "Deleted", "Gone."))}, special="\\Trash"),
        "Spam": stub.Mailbox(1700000004, {}),
        "Drafts": stub.Mailbox(1700000005, {}, special="\\Drafts"),
        "Projects": stub.Mailbox(1, {}, noselect=True),
    }


class Env:
    def __init__(self, tmp_path: Path):
        self.state = stub.State(mailboxes(), password=PASSWORD)
        self.server = stub.Server(self.state).__enter__()
        self.credentials = tmp_path / "credentials"
        (tmp_path / "towpath.toml").write_text('[stores]\ndir = "state"\n')
        self.config = config_mod.load(tmp_path / "towpath.toml")
        self.store = self.config.store_dir
        self.settings = {"host": "127.0.0.1", "port": self.server.port, "security": "plain-loopback",
                         "username": self.state.username}

    def add(self) -> str:
        folders = connections.test_imap(self.settings, PASSWORD)
        return connections.add_imap(self.store, self.credentials, "imap", self.settings, PASSWORD, folders)

    def assert_read_only(self):
        assert self.state.violations == []
        for line in self.state.transcript:
            command = line.split(" ", 1)[1]
            verb = command.split(" ")[1] if command.startswith("UID ") else command.split(" ")[0]
            assert verb.upper() not in WRITES, command

    def close(self):
        self.server.__exit__(None, None, None)


@pytest.fixture
def env(tmp_path):
    e = Env(tmp_path)
    yield e
    e.close()


def everything_written(env) -> str:
    """Every byte of every store, for checking that no secret was written anywhere but its own file."""
    return "".join(p.read_bytes().decode("latin-1") for p in env.store.glob("*.db"))


def test_a_test_lists_folders_with_counts_and_sensible_defaults(env):
    folders = {f["name"]: f for f in connections.test_imap(env.settings, PASSWORD)}
    assert set(folders) == {"INBOX", "Archive", "Trash", "Spam", "Drafts"}  # \\Noselect is not offered
    assert folders["INBOX"]["messages"] == 7 and folders["Archive"]["messages"] == 1
    assert [n for n, f in folders.items() if f["include"]] == ["Archive", "INBOX"]
    assert folders["Trash"]["special"] == "Trash"
    env.assert_read_only()


@pytest.mark.parametrize("change, message", [
    ({"password": "wrong"}, "rejected the address or password"),
    ({"host": "no-such-host.invalid", "security": "tls"}, "Couldn't find the server"),
    ({"port": 1}, "Couldn't connect to 127.0.0.1:1"),
    ({"host": "203.0.113.1", "security": "plain-loopback"}, "loopback"),
])
def test_problems_are_explained_in_plain_language(env, change, message):
    password = change.pop("password", PASSWORD)
    with pytest.raises(connections.ConnectionProblem) as caught:
        connections.test_imap({**env.settings, **change}, password)
    assert message in str(caught.value) and PASSWORD not in str(caught.value)


def test_the_password_lives_only_in_its_own_private_file(env):
    sid = env.add()
    path = connections.secret_path(env.credentials, sid)
    assert path.read_text() == PASSWORD and stat.S_IMODE(path.stat().st_mode) == 0o600
    assert PASSWORD not in everything_written(env)
    web_view = connections.merged(env.config, None, role="web").sources[sid]
    assert web_view.credential == "none"  # the web service holds no way to find the secret
    assert connections.merged(env.config, env.credentials).sources[sid].credential == f"file:{path}"


def test_folders_then_indexing_in_slices_then_incremental(env, monkeypatch):
    sid = env.add()
    assert connections.get(env.store, sid)["state"] == "choose-folders"
    with pytest.raises(connections.ConnectionProblem, match="Choose folders first"):
        connections.start_indexing(env.store, env.credentials, sid)
    connections.choose_folders(env.store, sid, ["INBOX", "Archive", "Not a folder"])
    assert connections.get(env.store, sid)["settings"]["mailboxes"] == ["INBOX", "Archive"]
    # Nothing happens until the owner starts indexing.
    assert connections.index_pending(env.config, env.credentials) == []
    connections.start_indexing(env.store, env.credentials, sid)

    ticks = iter(range(0, 10_000, 100))  # every item read advances the clock past the slice deadline
    first = connections.index_pending(env.config, env.credentials, slice_seconds=250, clock=lambda: next(ticks))
    assert first[0]["slice"] == "slice" and connections.get(env.store, sid)["indexing"] == "running"
    assert 0 < first[0]["indexed_total"] < 8
    while connections.get(env.store, sid)["indexing"] == "running":
        connections.index_pending(env.config, env.credentials)
    found = connections.get(env.store, sid)
    assert found["indexing"] == "idle" and found["progress"]["indexed"] == 8 and found["last_error"] is None
    with closing(open_store(env.store, "source", "web")) as db:
        boxes = {r[0] for r in db.execute("SELECT labels FROM observations")}
    assert all("Trash" not in b for b in boxes)
    # A later run reads only what is new.
    env.state.mailboxes["INBOX"].messages[8] = stub.Message(stub.message(40, "New report", "Fresh."))
    env.state.transcript.clear()
    connections.start_indexing(env.store, env.credentials, sid)
    connections.index_pending(env.config, env.credentials)
    assert connections.get(env.store, sid)["progress"]["indexed"] == 9
    assert sum("FETCH" in line for line in env.state.transcript) == 1
    env.assert_read_only()


def test_pause_stops_within_the_slice_and_resume_continues(env):
    sid = env.add()
    connections.choose_folders(env.store, sid, ["INBOX"])
    connections.start_indexing(env.store, env.credentials, sid)
    clock = iter(range(0, 10_000, 5))
    calls = {"n": 0}
    real_get = connections.get

    def owner_pauses(store_dir, source_id, role="connect"):
        calls["n"] += 1
        if calls["n"] == 3:
            connections.pause_indexing(store_dir, source_id)
        return real_get(store_dir, source_id, role)

    connections.get, saved = owner_pauses, connections.get
    try:
        result = connections.index_pending(env.config, env.credentials, clock=lambda: next(clock))
    finally:
        connections.get = saved
    assert result[0]["slice"] == "paused" and connections.get(env.store, sid)["indexing"] == "paused"
    assert result[0]["indexed_total"] < 7
    assert connections.index_pending(env.config, env.credentials) == []  # paused stays paused
    connections.start_indexing(env.store, env.credentials, sid)
    connections.index_pending(env.config, env.credentials)
    assert connections.get(env.store, sid)["progress"]["indexed"] == 7


def test_a_rejected_password_stops_cleanly_and_can_be_replaced(env):
    sid = env.add()
    connections.choose_folders(env.store, sid, ["INBOX"])
    connections.start_indexing(env.store, env.credentials, sid)
    env.state.password = "changed-at-the-provider"
    connections.index_pending(env.config, env.credentials)
    found = connections.get(env.store, sid)
    assert found["indexing"] == "idle" and "replace the password" in found["last_error"]
    folders = connections.test_imap(env.settings, "changed-at-the-provider")
    connections.replace_password(env.store, env.credentials, sid, "changed-at-the-provider", folders)
    connections.start_indexing(env.store, env.credentials, sid)
    connections.index_pending(env.config, env.credentials)
    assert connections.get(env.store, sid)["progress"]["indexed"] == 7
    assert "changed-at-the-provider" not in everything_written(env)


def test_disconnect_removes_the_secret_and_keeps_the_index(env):
    sid = env.add()
    connections.choose_folders(env.store, sid, ["INBOX"])
    connections.start_indexing(env.store, env.credentials, sid)
    connections.index_pending(env.config, env.credentials)
    connections.disconnect(env.store, env.credentials, sid)
    assert not connections.secret_path(env.credentials, sid).exists()
    assert connections.get(env.store, sid)["state"] == "disconnected"
    with pytest.raises(connections.ConnectionProblem):
        connections.start_indexing(env.store, env.credentials, sid)
    with closing(open_store(env.store, "source", "web")) as db:
        assert db.execute("SELECT COUNT(*) FROM items WHERE source_id = ?", (sid,)).fetchone()[0] == 7


def test_the_request_worker_runs_requested_indexing(env):
    sid = env.add()
    connections.choose_folders(env.store, sid, ["INBOX", "Archive"])
    connections.start_indexing(env.store, env.credentials, sid)
    result = request_worker.run_once(env.config, env.credentials)
    assert result["indexing"] == [{"source_id": sid, "termination": "complete", "slice": None, "indexed_total": 8}]
    assert request_worker.run_once(env.config, env.credentials)["indexing"] == []  # idle polls do nothing


def test_a_schedule_the_owner_chooses_starts_checks_by_itself(env):
    from datetime import datetime, timedelta, timezone

    sid = env.add()
    assert connections.start_scheduled(env.store, env.credentials) == []  # nothing is scheduled by default
    with pytest.raises(connections.ConnectionProblem):
        connections.set_schedule(env.store, sid, "7d")  # weekly is offered for folders only
    connections.set_schedule(env.store, sid, "1h")
    assert connections.start_scheduled(env.store, env.credentials) == []  # folders not chosen yet
    connections.choose_folders(env.store, sid, ["INBOX", "Archive"])
    result = request_worker.run_once(env.config, env.credentials)  # never checked: due now
    assert result["indexing"][0] == {"source_id": sid, "step": "scheduled"}
    assert result["indexing"][1]["termination"] == "complete" and result["indexing"][1]["indexed_total"] == 8
    assert request_worker.run_once(env.config, env.credentials)["indexing"] == []  # not due for an hour
    later = datetime.now(timezone.utc) + timedelta(minutes=61)
    assert connections.start_scheduled(env.store, env.credentials, now=later) == [{"source_id": sid,
                                                                                    "step": "scheduled"}]
    connections.pause_indexing(env.store, sid)
    assert connections.start_scheduled(env.store, env.credentials, now=later + timedelta(hours=2)) == []  # paused
    connections.set_schedule(env.store, sid, "off")
    assert connections.next_check(env.store, connections.get(env.store, sid)) is None
    env.assert_read_only()


def test_an_ai_model_connection_is_tested_saved_and_handed_to_the_gateway(env, openai_stub):
    from towpath.models.profiles import fingerprint

    stub = openai_stub(model="local-9b")
    form = {"base_url": f"http://127.0.0.1:{stub.port}/v1", "model": "local-9b"}
    with pytest.raises(connections.ConnectionProblem, match="can't use a model named"):
        connections.test_model({**form, "model": "other"}, "token-1")
    with pytest.raises(connections.ConnectionProblem, match="http://"):
        connections.test_model({**form, "base_url": "poundlock:7331"}, "token-1")
    tested = connections.test_model(form, "token-1")
    assert [r["path"] for r in stub.requests] == ["/v1/models", "/v1/models", "/v1/chat/completions"]
    assert stub.chat_requests()[0]["body"]["messages"][0]["content"] == "Reply with the word ok."  # synthetic only
    sid = connections.add_model(env.store, env.credentials, form, "token-1", tested)
    assert (env.credentials / f"{sid}.password").read_text() == "token-1"
    assert "token-1" not in everything_written(env)
    worker = connections.merged(env.config, env.credentials)
    web = connections.merged(env.config, None, role="web")
    assert worker.tasks["life.propose"] == sid and worker.endpoints[sid].model == "local-9b"
    assert worker.endpoints[sid].destination == "self-hosted"  # personal data needs the owner's grant
    assert fingerprint(worker.endpoints[sid]) == fingerprint(web.endpoints[sid])  # a web grant matches the worker
    assert connections.start_scheduled(env.store, env.credentials) == []  # a model is never indexed
