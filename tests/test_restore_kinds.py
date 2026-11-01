"""Restoring every kind of decision the owner makes, an account that is connected only later, and packages the
reader refuses before anything is planned."""

import json
import shutil
from contextlib import closing

import pytest

from tests import test_restore
from tests.test_restore import _add, _edited, _zip, decided, fresh
from towpath import decisions, export, life, life_ask, life_conflicts, life_groups, restore, space
from towpath.mail import senders, threads
from towpath.stores import open_store, store_path
from towpath.unified import collections



@pytest.fixture
def world(tmp_path, openai_stub):
    yield from test_restore.world.__wrapped__(tmp_path, openai_stub)


def _setting(store, target, key):
    with closing(open_store(store, "decisions", "web")) as db:
        return db.execute("SELECT value FROM settings WHERE target_id = ? AND key = ?", (target, key)).fetchone()


def test_every_kind_of_owner_decision_comes_back_once(world):
    a, b = (c.claim_id for c in life.claims(world.store)[:2])
    life_groups.set_profile(world.store, "Sam Example", "1980-05-01")
    life_conflicts.both_right(world.store, a, b)
    decisions.mark_folder(world.store, "files", "archive", "Old taxes", keep=True)
    life_ask.ask(world.store, "When did we first visit Example Junction?")
    kept_set = collections.create(world.store, "Trip papers", "set")
    collections.add(world.store, kept_set["collection_id"], f"{world.sid}:msg-1", title="Booking")
    collections.create(world.store, "Fuji only", "photos", photos={"camera": "x100v"})
    _, data = export.build(world.store)
    at = {"author": "owner", "at": "2026-01-01T00:00:00+00:00"}
    data = _edited(data, lambda files: _add(files, "dismissals",
                                            {"match_key": "mk_1", "proposal_id": "dprop_1", "reason": "a copy", **at}))
    fresh(world)

    done = restore.apply(world.store, data)
    assert life_groups.profile(world.store) == {"name": "Sam Example", "birthday": "1980-05-01"}
    assert life_conflicts.settled(world.store) and len(life_conflicts.settled(world.store)) == 1
    assert _setting(world.store, space.keep_target("files", "archive", "Old taxes"), space.KEEP_KEY)
    assert [q["text"] for q in life_ask.questions(world.store)] == ["When did we first visit Example Junction?"]
    with closing(open_store(world.store, "decisions", "web")) as db:
        assert [tuple(r) for r in db.execute("SELECT match_key, reason FROM dismissals")] == [("mk_1", "a copy")]
    found = {c["name"]: collections.get(world.store, c["collection_id"]) for c in collections.list_all(world.store)}
    assert [m["ref"] for m in found["Trip papers"]["members"]] == [f"{world.sid}:msg-1"]
    assert found["Fuji only"]["filters"] == {"camera": "x100v"} and not found["Fuji only"]["members"]

    again = restore.plan(world.store, data)
    assert again["total"] == 0 and again["kept"] >= done["total"]  # each decision is already here


def test_decisions_for_an_account_not_connected_here_wait_until_it_is(world, tmp_path):
    decided(world)
    _, data = export.build(world.store)
    fresh(world)
    connections = store_path(world.store, "connections")
    shutil.move(connections, tmp_path / "connections.db")  # the account isn't connected here yet

    plan = restore.plan(world.store, data)
    assert plan["waiting"] and not [n for label, n in plan["adds"] if label == "sender categories"]
    restore.apply(world.store, data)
    assert senders.marks(world.store, world.sid, "web") == {}

    shutil.move(tmp_path / "connections.db", connections)  # connected again: the same file restores the rest
    restore.apply(world.store, data)
    assert senders.marks(world.store, world.sid, "web") == {"sam@example.org": "person"}
    assert threads.notes(world.store, world.sid, "web") == "Sign off with just my first name."


@pytest.mark.parametrize("files, limits, message", [
    ({"restore/version.json": json.dumps({"version": restore.VERSION + 1}).encode()}, {}, "different version"),
    ({"restore/version.json": b'{"version": 1}', "restore/settings.json": b'{"not": "a list"}'}, {},
     "settings.json in that export can't be read"),
    ({"restore/version.json": b'{"version": 1}', "restore/settings.json": b"[" + b" " * 200 + b"]"},
     {"MAX_MEMBER": 100}, "too large"),
    ({"restore/version.json": b'{"version": 1}', "restore/settings.json": b"[]" + b" " * 90,
      "restore/questions.json": b"[]" + b" " * 90}, {"MAX_TOTAL": 150}, "too large"),
])
def test_packages_beyond_the_limits_or_from_another_version_are_refused(tmp_path, monkeypatch, files, limits,
                                                                         message):
    for name, value in limits.items():
        monkeypatch.setattr(restore.reading, name, value)
    with pytest.raises(restore.RestoreError, match=message):
        restore.plan(tmp_path, _zip(files))
