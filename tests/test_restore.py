"""Restoring the owner's decisions from an export: shown first, only ever added, and never bringing back anything
that could change mail, reach a model or widen access."""

import io
import json
import re
import sys
import zipfile
from contextlib import closing
from pathlib import Path

import pytest
from django.test import Client, override_settings

from towpath import decisions, export, life, life_propose, restore
from towpath.mail import rules, senders, settings as mail_settings, threads
from towpath.stores import open_store, store_path
from towpath.unified import collections, grants
from towpath.web import auth
from towpath.web.application import configure

sys.path.insert(0, str(Path(__file__).parent))
import test_life_memories  # noqa: E402

OWNER = test_life_memories.OWNER


@pytest.fixture
def world(tmp_path, openai_stub):
    yield from test_life_memories.world.__wrapped__(tmp_path, openai_stub)


def decided(w):
    """A lived-in Towpath: the owner's decisions, and switches a file must never turn back on."""
    first = life.claims(w.store)[0].claim_id
    life.review(w.store, first, "reject")
    life.recollect(w.store, "Learned to swim", "1987", "year", "1987-01-01", "1987-12-31")
    senders.mark(w.store, w.sid, "sam@example.org", "person")
    threads.set_notes(w.store, w.sid, "Sign off with just my first name.")
    rules.save(w.store, w.sid, rules.TEMPLATES["marketing"] | {"scope": "all"}, mode="auto")
    mail_settings.set_dry_run(w.store, w.sid, False)
    mail_settings.set_enabled_types(w.store, w.sid, {"archive"})
    decisions.set_task_model(w.store, "life.propose", w.model, author="owner")
    life_propose.set_auto(w.store, True)
    return first


def fresh(w):
    """The same connections, with every decision gone (a new Towpath the owner added the account to again)."""
    for store in ("decisions", "maildecisions"):
        store_path(w.store, store).unlink(missing_ok=True)


def test_a_restore_brings_back_the_owners_decisions_and_nothing_that_changes_mail(world):
    first = decided(world)
    _, data = export.build(world.store)
    fresh(world)
    plan = restore.plan(world.store, data)
    assert plan["total"] >= 5 and not life.reviews(world.store, [first])  # a plan changes nothing
    done = restore.apply(world.store, data)
    assert done["total"] == plan["total"]
    assert life.reviews(world.store, [first])[first]["state"] == "rejected"
    assert "Learned to swim" in {c.statement for c in life.recollections(world.store)}
    assert senders.marks(world.store, world.sid, "web") == {"sam@example.org": "person"}
    assert threads.notes(world.store, world.sid, "web") == "Sign off with just my first name."
    (rule,) = rules.all_rules(world.store, world.sid, "web")
    assert rule["enabled"] == 0 and rule["mode"] == "propose"  # back for review, never running by itself
    assert mail_settings.dry_run(world.store, world.sid, "web") is True
    assert mail_settings.enabled_types(world.store, world.sid, "web") == set()
    assert decisions.task_models(world.store) == {} and not life_propose.auto(world.store, "web")
    assert grants.granted(world.store) == {}  # permissions are given again, never read from a file
    assert restore.apply(world.store, data)["total"] == 0  # restoring twice adds nothing


def test_a_restore_never_replaces_what_the_owner_decided_here(world):
    first = decided(world)
    _, data = export.build(world.store)
    fresh(world)
    life.review(world.store, first, "accept")
    senders.mark(world.store, world.sid, "sam@example.org", "newsletter")
    plan = restore.plan(world.store, data)
    assert plan["kept"] >= 2
    restore.apply(world.store, data)
    assert life.reviews(world.store, [first])[first]["state"] == "accepted"
    assert senders.marks(world.store, world.sid, "web")["sam@example.org"] == "newsletter"


def test_a_package_cannot_smuggle_in_permissions_switches_or_unknown_accounts(world):
    decided(world)
    _, data = export.build(world.store)
    z = zipfile.ZipFile(io.BytesIO(data))
    files = {n: z.read(n) for n in z.namelist()}
    extra = json.loads(files["restore/settings.json"])
    extra += [{"target_id": mail_settings.target(world.sid), "key": "dry_run", "value": "false", "author": "x",
               "at": "2026-01-01"},
              {"target_id": "models:tasks", "key": "tasks", "value": "{}", "author": "x", "at": "2026-01-01"},
              {"target_id": "item:1", "key": "model_use", "value": "send-everywhere", "author": "x",
               "at": "2026-01-01"}]
    files["restore/settings.json"] = json.dumps(extra).encode()
    marks = json.loads(files["restore/sender_marks.json"])
    marks.append({"source_id": "elsewhere", "address": "a@example.org", "category": "person", "author": "x",
                  "at": "2026-01-01"})
    files["restore/sender_marks.json"] = json.dumps(marks).encode()
    accounts = json.loads(files["restore/accounts.json"])
    accounts.append({"source_id": "gmail", "provider": "gmail", "display_name": "Not here yet"})
    files["restore/accounts.json"] = json.dumps(accounts).encode()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as out:
        for n, b in files.items():
            out.writestr(n, b)
    fresh(world)
    plan = restore.plan(world.store, buf.getvalue())
    assert plan["skipped"] >= 4 and plan["waiting"] == []
    restore.apply(world.store, buf.getvalue())
    assert mail_settings.dry_run(world.store, world.sid, "web") is True
    with pytest.raises(restore.RestoreError):
        restore.plan(world.store, b"not a zip")
    with pytest.raises(restore.RestoreError):
        restore.plan(world.store, _zip({"README.md": b"hello"}))


def _zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n, b in files.items():
            z.writestr(n, b)
    return buf.getvalue()


def test_the_restore_page_shows_the_plan_then_restores(world, monkeypatch):
    first = decided(world)
    _, data = export.build(world.store)
    fresh(world)
    monkeypatch.setattr(auth, "_setup_codes", {})
    configure(world.store)
    auth.create_account(world.store, "owner", OWNER)
    with override_settings(TOWPATH_STORE_DIR=world.store, TOWPATH_CONFIG=world.config, ALLOWED_HOSTS=["testserver"]):
        client = Client()
        client.post("/login", {"username": "owner", "password": OWNER})
        upload = io.BytesIO(data)
        upload.name = "towpath-export.zip"
        page = client.post("/restore", {"package": upload}).content.decode()
        assert "What this export would add" in page and "Never restored" in page
        assert not life.reviews(world.store, [first])
        digest = re.search(r'name="digest" value="(\w+)"', page)[1]
        page = client.post("/restore", {"action": "restore", "digest": digest}).content.decode()
        assert "Restored" in page and life.reviews(world.store, [first])[first]["state"] == "rejected"
        assert not (Path(world.store) / "pending-restore.zip").exists()


def _edited(data, change):
    z = zipfile.ZipFile(io.BytesIO(data))
    files = {n: z.read(n) for n in z.namelist()}
    change(files)
    return _zip(files)


def _add(files, name, *rows):
    files[f"restore/{name}.json"] = json.dumps(json.loads(files[f"restore/{name}.json"]) + list(rows)).encode()


def test_damaged_or_hand_edited_rows_are_left_out_and_cannot_break_a_page(world):
    first = decided(world)
    _, data = export.build(world.store)
    at = {"author": "x", "at": "2026-01-01"}

    def damage(files):
        _add(files, "settings",
             {"target_id": "claim:clm_x", "key": "review", "value": json.dumps({"state": "accepted"}), **at},
             {"target_id": "group:g1", "key": "group", "value": "{}", **at},
             {"target_id": mail_settings.target(world.sid), "key": "draft_notes", "value": json.dumps(["x"]), **at},
             {"target_id": mail_settings.target(world.sid), "key": "draft_notes", "value": json.dumps("x" * 5000),
              **at})
        _add(files, "collections",
             {"collection_id": "col_bad1", "name": "Bad JSON", "kind": "query", "definition": "{not json",
              "author": "x", "created_at": "t", "updated_at": "t"},
             {"collection_id": "col_bad2", "name": "Bad kind", "kind": "anything", "definition": None,
              "author": "x", "created_at": "t", "updated_at": "t"},
             {"collection_id": "col_bad3", "name": "Photos as query", "kind": "query",
              "definition": json.dumps({"camera": "x"}), "author": "x", "created_at": "t", "updated_at": "t"})
        _add(files, "dismissals", {"match_key": "m1", "reason": ["not text"], **at})
    damaged = _edited(data, damage)
    fresh(world)
    plan = restore.plan(world.store, damaged)
    assert plan["skipped"] >= 8
    restore.apply(world.store, damaged)
    assert life.reviews(world.store, [first])[first]["state"] == "rejected"  # the good rows still came back
    assert threads.notes(world.store, world.sid, "web") == "Sign off with just my first name."
    assert life.claims(world.store)
    assert not [c for c in collections.list_all(world.store) if c["name"].startswith("Bad")]
    for bad in (b"PK\x03\x04" + b"\x00" * 40, _zip({"restore/version.json": b"[" * 100_000})):
        with pytest.raises(restore.RestoreError):
            restore.plan(world.store, bad)


def test_marks_that_keep_mail_from_cloud_models_follow_the_account_to_its_new_id(world):
    from towpath.connect import item_id
    from towpath.mail import privacy

    with closing(open_store(world.store, "source", "web")) as src:
        item, native = src.execute("SELECT item_id, native_id FROM items WHERE source_id = ? LIMIT 1",
                                   (world.sid,)).fetchone()
    decisions.set_item(world.config, privacy.sender_target(world.sid, "sam@example.org"), model_use="no-cloud")
    decisions.set_item(world.config, item, model_use="local-only")
    _, data = export.build(world.store)
    fresh(world)
    with closing(open_store(world.store, "connections", "connect")) as db:
        db.execute("UPDATE connections SET source_id = 'fastmail-new' WHERE source_id = ?", (world.sid,))
        db.commit()
    restore.apply(world.store, data)
    with closing(open_store(world.store, "decisions", "web")) as db:
        got = dict(db.execute("SELECT target_id, value FROM settings WHERE key = 'model_use'").fetchall())
    assert got == {privacy.sender_target("fastmail-new", "sam@example.org"): "no-cloud",
                   item_id("fastmail-new", native): "local-only"}


def test_the_restore_button_applies_only_the_file_whose_plan_was_shown(world, monkeypatch):
    decided(world)
    _, data = export.build(world.store)
    fresh(world)
    monkeypatch.setattr(auth, "_setup_codes", {})
    configure(world.store)
    auth.create_account(world.store, "owner", OWNER)
    with override_settings(TOWPATH_STORE_DIR=world.store, TOWPATH_CONFIG=world.config, ALLOWED_HOSTS=["testserver"]):
        client = Client()
        client.post("/login", {"username": "owner", "password": OWNER})
        upload = io.BytesIO(data)
        upload.name = "a.zip"
        client.post("/restore", {"package": upload})
        page = client.post("/restore", {"action": "restore", "digest": "0" * 64}).content.decode()
        assert "changed since its plan was shown" in page and not senders.marks(world.store, world.sid, "web")
