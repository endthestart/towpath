"""Read-only IMAP connector against a synthetic IMAP server: identity, reconciliation, no side effects."""

import pytest

from tests.imap_env import make_env, stub
from towpath import config as config_mod
from towpath.adapters import build_connector
from towpath.adapters.imap import ReadOnlySession, ReadOnlyViolation
from towpath.stores import open_store
from towpath.unified.contracts import parse_imap_native



@pytest.fixture
def env(tmp_path, monkeypatch):
    yield from make_env(tmp_path, monkeypatch)


def test_full_sync_indexes_every_selectable_mailbox_read_only(env):
    flags_before = env.state.flags()
    result = env.sync()
    assert result["termination"] == "complete" and result["complete"] and result["indexed_total"] == 3
    items = env.items()
    identities = {parse_imap_native(n) for n in items}
    assert identities == {("INBOX", 1700000001, 1), ("INBOX", 1700000001, 2), ("Archive/2008", 1700000002, 3)}
    subjects = {i["subject"] for i in items.values()}
    assert "Grüße vom Kanal" in subjects
    env.assert_read_only()
    assert env.state.flags() == flags_before  # \Seen neither added nor removed
    assert any(c.startswith("EXAMINE") for c in env.commands())
    assert not any(c.startswith("EXAMINE \"Projects\"") for c in env.commands())  # \Noselect skipped


def test_parts_carry_imap_numbering_filenames_and_no_bodies(env):
    env.sync()
    db = open_store(env.config.store_dir, "source", "web")
    rows = [dict(r) for r in db.execute("SELECT p.* FROM parts p JOIN items i USING (item_id) "
                                        "WHERE i.native_id LIKE 'INBOX;%UID=2' ORDER BY part_id")]
    db.close()
    assert [(r["part_id"], r["mime_type"], r["filename"], r["disposition"]) for r in rows] == [
        ("1", "text/plain", None, None), ("2", "image/x-nikon-nef", "DSC_0042.NEF", "attachment")]
    assert all(r["sha256"] is None for r in rows)  # nothing fetched while indexing


def test_second_run_reads_only_new_messages_and_marks_expunged_absent(env):
    env.sync()
    env.state.transcript.clear()
    inbox = env.state.mailboxes["INBOX"]
    inbox.messages[4] = stub.Message(stub.message(4, "New minutes", "Fresh text."))
    del inbox.messages[1]
    result = env.sync()
    assert result["termination"] == "complete" and result["new"] == 1 and result["absent"] == 1
    fetches = [c for c in env.commands() if "FETCH" in c]
    assert fetches == ["UID FETCH 4 (RFC822.SIZE INTERNALDATE BODYSTRUCTURE "
                       "BODY.PEEK[HEADER.FIELDS (DATE SUBJECT FROM MESSAGE-ID)])"]
    present = {parse_imap_native(n)[2] for n in env.present()}
    assert present == {2, 3, 4}
    env.assert_read_only()


def test_uidvalidity_change_never_maps_old_uids_onto_new_ones(env):
    env.sync()
    old = env.present()
    inbox = env.state.mailboxes["INBOX"]
    inbox.uidvalidity = 1800000000
    inbox.messages = {10: inbox.messages[2], 11: inbox.messages[1]}  # same bytes, renumbered
    result = env.sync()
    assert result["termination"] == "complete"
    now = env.present()
    assert {parse_imap_native(n) for n in old - now} == {("INBOX", 1700000001, 1), ("INBOX", 1700000001, 2)}
    assert {parse_imap_native(n) for n in now - old} == {("INBOX", 1800000000, 10), ("INBOX", 1800000000, 11)}
    assert len(env.items()) == 5  # the old identities remain as absent history, not rewritten


def test_deleted_or_renamed_mailbox_marks_only_its_items_absent(env):
    env.sync()
    env.state.mailboxes["Archive/Old"] = env.state.mailboxes.pop("Archive/2008")
    env.sync()
    present = {parse_imap_native(n)[:2] for n in env.present()}
    assert ("Archive/2008", 1700000002) not in present and ("Archive/Old", 1700000002) in present
    assert len([n for n in env.present() if n.startswith("INBOX")]) == 2


def test_a_mailbox_that_fails_to_open_is_never_reconciled_as_empty(env):
    env.sync()
    env.state.fail_examine = {"Archive/2008"}
    with pytest.raises(Exception):  # noqa: B017 - recorded as an error run, then re-raised
        env.sync()
    assert len(env.present()) == 3
    db = open_store(env.config.store_dir, "source", "web")
    last = db.execute("SELECT termination, complete FROM runs ORDER BY seq DESC LIMIT 1").fetchone()
    db.close()
    assert (last["termination"], last["complete"]) == ("error", 0)


def test_dropped_connection_stops_cleanly_and_the_next_run_resumes(env):
    env.state.drop_after_fetches = 1
    first = env.sync()
    assert first["termination"] == "server-stop" and not first["complete"]
    env.state.drop_after_fetches = None
    second = env.sync()
    assert second["termination"] == "complete" and len(env.present()) == 3
    assert len(env.items()) == 3


def test_capped_run_resumes_without_duplicates(env):
    first = env.sync(max_items=1)
    assert first["termination"] == "capped" and len(env.items()) == 1
    second = env.sync()
    assert second["termination"] == "complete" and len(env.items()) == 3 and len(env.present()) == 3


def test_fetch_batches_respect_server_limits(env, monkeypatch):
    env.state.max_fetch_ids = 1
    monkeypatch.setattr("towpath.adapters.imap.connector.FETCH_BATCH", 1)
    env.config = config_mod.load(env.path)
    connector = build_connector(env.config.sources["imap-test"], env.config)
    connector.batch = 1
    events = list(connector.enumerate(None))
    connector.close()
    assert len([e for e in events if e[0] == "item"]) == 3


def test_server_granting_write_access_on_examine_is_refused(env):
    env.state.grant_read_write = True
    with pytest.raises(ReadOnlyViolation):
        env.sync()
    assert not any("FETCH" in c for c in env.commands())


def test_wrong_password_is_a_clean_auth_stop(env, monkeypatch):
    monkeypatch.setenv("TOWPATH_TEST_IMAP_PASSWORD", "not-the-password")
    result = env.sync()
    assert result["termination"] == "auth-stop" and env.items() == {}
    assert all("not-the-password" not in line for line in env.state.transcript)


def test_session_facade_refuses_state_changing_fetches_and_hides_the_client():
    class Client:
        normalise_times = True

        def fetch(self, uids, items):
            raise AssertionError("must not be reached")

    session = ReadOnlySession(Client())
    for item in ("BODY[1]", "BODY[]", "RFC822", "RFC822.TEXT", "BODY.PEEK[1] FLAGS"):
        with pytest.raises(ReadOnlyViolation):
            session.fetch([1], (item,))
    for modifiers in (("UNCHANGEDSINCE 5",), ("CHANGEDSINCE 5 VANISHED",)):
        with pytest.raises(ReadOnlyViolation):
            session.fetch([1], ("FLAGS",), modifiers)
    with pytest.raises(ReadOnlyViolation):
        session.fetch("1:5", ("FLAGS",))
    with pytest.raises(ReadOnlyViolation):
        session.enable("QRESYNC")
    public = {n for n in dir(session) if not n.startswith("_")}
    assert public == {"login", "capabilities", "mailboxes", "folders", "examine", "search_all", "search_text",
                      "fetch", "logout", "enable", "search_message_id", "wait_for_changes"}
