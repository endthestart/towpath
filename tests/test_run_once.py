"""The owner's one bounded run: for its time background work may use the model fully, then finding events stops by
itself and the run can't be started again. Invented mail and a stand-in model."""

import json
import sqlite3
import sys
import time
from contextlib import closing
from datetime import timedelta
from pathlib import Path

import pytest

pytest.importorskip("openai")
pytest.importorskip("jsonschema")

from towpath import connections, decisions, life_groups, life_propose, request_worker  # noqa: E402
from towpath.life_propose import bounded_run  # noqa: E402
from towpath.mail import purchases, senders  # noqa: E402
from towpath.models import bulk, gateway, pacing  # noqa: E402
from towpath.stores import open_store  # noqa: E402
from towpath.unified import grants  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from tests.life_world import make_world  # noqa: E402
from test_life_propose import answer, email_prompts, world  # noqa: E402, F401
from test_life_web import life_page  # noqa: E402, F401
from test_model_yield import local  # noqa: E402


@pytest.fixture
def mail_world(tmp_path, openai_stub):
    yield from make_world(tmp_path, openai_stub)


def after_the_window(monkeypatch):
    at = bulk._now() + timedelta(hours=bulk.HOURS + 1)
    monkeypatch.setattr(bulk, "_now", lambda: at)


def allowed(config, credentials, sid, mid):
    grants.grant(config.store_dir, sid, "life-evidence")
    decisions.grant_model(connections.merged(config, None, role="web"), mid, "content")
    return connections.merged(config, credentials)


def memory_prompts(mail_world):
    return [p for p in mail_world.model_prompts() if "see their life as memories" in p]


def test_the_quarter_hour_share_is_lifted_only_while_the_run_is_open(ws, openai_stub, monkeypatch):
    config = local(ws, openai_stub())
    at = time.monotonic()
    pacing.record(config.store_dir, "local", at - 900, None, clock=lambda: at)  # the hour's share is used
    assert gateway.background_wait(config, "doc.is_statement") > 0
    assert life_propose.start_run(config.store_dir)
    assert gateway.background_wait(config, "doc.is_statement") == 0
    after_the_window(monkeypatch)
    assert gateway.background_wait(config, "doc.is_statement") > 0


def test_a_busy_model_still_holds_background_work_during_the_run(ws, openai_stub):
    config = local(ws, openai_stub())
    assert life_propose.start_run(config.store_dir)
    pacing.record(config.store_dir, "local", time.monotonic(), "busy")
    assert gateway.background_wait(config, "doc.is_statement") > 0


def test_the_run_finds_events_then_stops_by_itself_for_good(world, monkeypatch):  # noqa: F811
    config, credentials, sid, mid, model, state = world
    worker = allowed(config, credentials, sid, mid)
    model.answers = [answer()] * 10
    life_propose.set_auto(config.store_dir, True)  # left on from before: the run's end still stops it
    assert life_propose.start_run(config.store_dir)
    assert life_propose.auto(config.store_dir)
    life_propose.propose_pending(worker, credentials)
    assert email_prompts(model)
    after_the_window(monkeypatch)
    assert not life_propose.auto(config.store_dir)
    before = len(model.chat_requests())
    life_propose.propose_pending(worker, credentials)
    assert len(model.chat_requests()) == before
    assert life_propose.start_run(config.store_dir) is None
    assert not life_propose.auto(config.store_dir)


def test_stopping_ends_the_run_and_it_cannot_start_again(ws):
    store = ws.config.store_dir
    assert life_propose.start_run(store)
    life_propose.set_auto(store, False)
    assert not bulk.is_open(store) and not life_propose.auto(store)
    assert life_propose.start_run(store) is None
    assert not bulk.is_open(store)


def test_the_worker_records_what_was_left_and_what_the_run_did(world, monkeypatch, capsys):  # noqa: F811
    config, credentials, sid, mid, model, state = world
    worker = allowed(config, credentials, sid, mid)
    model.answers = [answer()] * 10
    assert life_propose.run_step(worker) == []  # nothing until the owner starts it
    assert life_propose.start_run(config.store_dir)
    capsys.readouterr()
    request_worker.run_once(config, credentials)  # printed at once: a long poll may never reach its own log line
    started = next(line for line in map(json.loads, capsys.readouterr().out.splitlines())
                   if line.get("step") == "run-once-started")
    assert started["left"]["emails"] >= 1 and started["left"]["years"] >= 1
    assert started["models"]["life.propose"] == "local-9b"
    assert life_propose.run_step(worker) == []  # recorded once
    for _ in range(5):
        life_propose.propose_pending(worker, credentials)
    after_the_window(monkeypatch)
    finished = life_propose.run_step(worker)
    assert [s["step"] for s in finished] == ["run-once-finished"]
    assert finished[0]["read"] >= 1 and finished[0]["left"]["emails"] == 0
    assert finished[0]["oldest_year_finished"] == "2009"
    assert life_propose.run_step(worker) == []
    assert "Example" not in json.dumps([started, finished])  # counts and model names, never mail


def test_a_problem_counting_never_stops_the_worker(world, monkeypatch):  # noqa: F811
    config, credentials, sid, mid, model, state = world
    worker = allowed(config, credentials, sid, mid)
    assert life_propose.start_run(config.store_dir)

    def broken(_config):
        raise RuntimeError("private detail")

    monkeypatch.setattr(bounded_run, "_left", broken)
    started = life_propose.run_step(worker)[0]
    assert started["left"] is None and started["problem"] == "Counting stopped on an unexpected error (RuntimeError)."


def test_a_store_problem_in_the_runs_own_record_never_stops_the_worker(world, monkeypatch):  # noqa: F811
    config, credentials, sid, mid, model, state = world
    worker = allowed(config, credentials, sid, mid)
    assert life_propose.start_run(config.store_dir)

    def locked(*_args):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(bounded_run, "auto_progress", locked)
    assert life_propose.run_step(worker) == [{"step": "run-once-problem", "error": "OperationalError"}]
    request_worker.run_once(config, credentials)


def test_a_leftover_paused_request_doesnt_stall_the_run(world):  # noqa: F811
    config, credentials, sid, mid, model, state = world
    worker = allowed(config, credentials, sid, mid)
    model.answers = [answer()] * 10
    life_propose.set_request(config.store_dir, sid, "2001-01-01", "2001-12-31")
    life_propose.pause(config.store_dir, True)
    assert life_propose.start_run(config.store_dir)
    life_propose.propose_pending(worker, credentials)
    assert email_prompts(model)


def test_a_leftover_request_for_an_account_gone_doesnt_stall_the_run(world):  # noqa: F811
    config, credentials, sid, mid, model, state = world
    worker = allowed(config, credentials, sid, mid)
    model.answers = [answer()] * 10
    life_propose.set_request(config.store_dir, "gone-account", "2001-01-01", "2001-12-31")
    assert life_propose.start_run(config.store_dir)
    life_propose.propose_pending(worker, credentials)
    assert email_prompts(model)


def test_the_result_counts_only_what_this_run_finished(world, monkeypatch):  # noqa: F811
    config, credentials, sid, mid, model, state = world
    worker = allowed(config, credentials, sid, mid)
    with closing(open_store(config.store_dir, "life", "worker")) as db:
        db.execute("INSERT INTO runs (run_id, source_id, slice, state, progress, started_at) VALUES (?,?,?,?,?,?)",
                   ("old", sid, json.dumps({"source_id": sid, "after": "1999-01-01", "before": "1999-12-31",
                                            "auto": True}), "done", json.dumps({"examined": 9000, "proposed": 3}),
                    "2020-01-01T00:00:00+00:00"))
        db.commit()
    assert life_propose.start_run(config.store_dir)
    assert life_propose.run_step(worker)[0]["read"] == 9000
    after_the_window(monkeypatch)
    finished = life_propose.run_step(worker)[0]
    assert finished["read"] == 0 and finished["events"] == 0 and finished["oldest_year_finished"] is None


def test_mail_backlogs_are_counted_from_the_stores(mail_world):
    worker = mail_world.worker()
    mail_world.track()
    assert bounded_run._left(worker)["conversations"] == 0
    with closing(open_store(mail_world.store, "mailintel", "worker")) as intel:
        waiting = intel.execute("UPDATE threads SET state_by = 'rule' WHERE kind = 'conversation'").rowcount
        moved = intel.execute("UPDATE senders SET category_by = 'rule', category = 'other', owner_wrote = 0").rowcount
        cleared = intel.execute("DELETE FROM drafts").rowcount
        intel.commit()
    left = bounded_run._left(worker)
    assert waiting and moved and cleared
    assert left["conversations"] == waiting and left["senders"] == moved and left["drafts"] == cleared
    senders.categorize_pending(worker)
    assert bounded_run._left(worker)["senders"] == 0


def receipts(store, *copies):
    with closing(open_store(store, "mail", "connect")) as mail:
        for uid, mailbox in copies:
            mail.execute("INSERT INTO messages (item_id, source_id, mailbox, uidvalidity, uid, message_id, thread_key, "
                         "from_addr, subject, date_utc, text, analyzed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                         (f"itm{uid}", "s", mailbox, 1, uid, "<receipt@example.org>", "t", "shop@example.org",
                          "Your order receipt", "2024-01-01T00:00:00+00:00", "Total $12.00", "2024-01-01"))
        mail.commit()


def purchase_rows(store):
    with closing(open_store(store, "mailintel", "worker")) as intel:
        return [tuple(r) for r in intel.execute("SELECT item_id, model FROM purchases ORDER BY item_id")]


def test_counting_purchases_changes_nothing(tmp_path):
    receipts(tmp_path, (1, "INBOX"), (2, "Archive"))
    with closing(open_store(tmp_path, "mailintel", "worker")) as intel, \
            closing(open_store(tmp_path, "mail", "worker")) as mail:
        assert purchases.waiting(mail, intel, "s", {}) == 1
    assert purchase_rows(tmp_path) == []


def test_a_copy_set_aside_never_hides_the_message_still_waiting_to_be_read(tmp_path):
    receipts(tmp_path, (1, "INBOX"), (2, "Archive"))
    with closing(open_store(tmp_path, "mailintel", "worker")) as intel, \
            closing(open_store(tmp_path, "mail", "worker")) as mail:
        first = purchases._candidates(mail, intel, "s", {}, 40)  # a slice that ends before the model reads it
        assert [m["item_id"] for m in first] == ["itm1"]
        assert purchase_rows(tmp_path) == [("itm2", "another-copy")]
        assert [m["item_id"] for m in purchases._candidates(mail, intel, "s", {}, 40)] == ["itm1"]


def test_memories_stop_with_the_run_until_the_owner_turns_finding_on_again(mail_world, monkeypatch):
    store, worker = mail_world.store, mail_world.worker()
    assert life_propose.start_run(store)
    life_groups.propose_groups(worker)
    assert memory_prompts(mail_world)
    after_the_window(monkeypatch)
    with closing(open_store(store, "life", "worker")) as db:
        db.execute("DELETE FROM group_runs")  # every month would be asked about again
        db.commit()
    asked = len(memory_prompts(mail_world))
    life_groups.propose_groups(worker)
    assert len(memory_prompts(mail_world)) == asked
    life_propose.set_auto(store, True)
    life_groups.propose_groups(worker)
    assert len(memory_prompts(mail_world)) > asked


def test_the_life_page_offers_the_run_once_only_when_it_can_read_and_shows_it(life_page):  # noqa: F811
    env, client, sid, model = life_page
    assert 'action="/life/run-once"' not in client.get("/life/").content.decode()
    assert client.post("/life/run-once").status_code == 409  # the one use isn't spent on nothing
    assert not bulk.window(env.store)
    client.post("/life/account", {"source_id": sid, "allow": "1"})
    client.post("/life/model", {"endpoint_id": model, "allow": "1"})
    page = client.get("/life/").content.decode()
    assert 'action="/life/run-once"' in page and "Run once" in page
    assert client.post("/life/run-once").status_code == 302
    worker = connections.merged(env.config, env.credentials)
    life_propose.run_step(worker)
    page = client.get("/life/").content.decode()
    assert 'action="/life/run-once"' not in page and "Stop finding events" in page
    assert "Run once is on until" in page and "At the start" in page
    assert "Run once is on until" in client.get("/life/status").content.decode()
    assert client.post("/life/run-once").status_code == 409
    client.post("/life/auto", {"on": "0"})
    page = client.get("/life/").content.decode()
    assert "Run once has ended" in page and 'action="/life/run-once"' not in page
    assert 'hx-get="/life/status"' in page  # still watching for the result the worker writes
    life_propose.run_step(worker)
    page = client.get("/life/").content.decode()
    assert "It read 0 messages" in page and 'hx-get="/life/status"' not in page


def test_the_life_page_says_when_counting_failed(life_page, monkeypatch):  # noqa: F811
    env, client, sid, model = life_page
    client.post("/life/account", {"source_id": sid, "allow": "1"})
    client.post("/life/model", {"endpoint_id": model, "allow": "1"})
    assert client.post("/life/run-once").status_code == 302

    def broken(_config):
        raise RuntimeError("private detail")

    monkeypatch.setattr(bounded_run, "_left", broken)
    worker = connections.merged(env.config, env.credentials)
    life_propose.run_step(worker)
    client.post("/life/auto", {"on": "0"})
    life_propose.run_step(worker)
    page = client.get("/life/").content.decode()
    assert page.count("Counting stopped on an unexpected error (RuntimeError).") == 2 and "private detail" not in page
