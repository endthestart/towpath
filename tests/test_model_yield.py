"""Background model work yields to everyone else: it never waits in line, takes at most a quarter of any hour of
the model, and after the model is busy or unreachable every background task waits together. A loopback stub."""

import json
import sys
from contextlib import closing
from pathlib import Path

import pytest

pytest.importorskip("openai")
pytest.importorskip("jsonschema")

from towpath import life_groups  # noqa: E402
from towpath.models import gateway, pacing  # noqa: E402
from towpath.stores import open_store  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from test_life_memories import world  # noqa: E402, F401 - the memories fixture
from test_models import SCHEMA, bind, endpoint  # noqa: E402


def local(ws, stub):
    bind(ws, endpoint("local", stub.url), {"doc.is_statement": "local", "life.group": "local"})
    gateway.probe(ws.config, "local")
    return ws.config


def ask(config, task="doc.is_statement", background=True):
    return gateway.infer(config, task, "synthetic test", "metadata", SCHEMA, background=background)


@pytest.mark.parametrize("code, failure", [(409, "busy"), (429, "transient"), (500, "transient"), (502, "transient")])
def test_a_busy_or_failing_model_ends_the_call_without_trying_other_formats(ws, openai_stub, code, failure):
    stub = openai_stub(errors=[code])
    config = local(ws, stub)
    before = len(stub.chat_requests())
    out = ask(config, background=False)
    assert out["status"] == "unavailable" and out["failure"] == failure
    assert len(stub.chat_requests()) == before + 1


def test_an_unreachable_model_ends_the_call_at_once(ws, openai_stub):
    stub = openai_stub()
    config = local(ws, stub)
    stub.close()
    out = ask(config, background=False)
    assert out["status"] == "unavailable" and out["failure"] == "transient" and "APIConnectionError" in out["reason"]


def test_background_calls_ask_not_to_wait_in_line_and_the_owners_calls_do_not(ws, openai_stub):
    stub = openai_stub()
    config = local(ws, stub)
    assert ask(config)["status"] == "ok" and ask(config, background=False)["status"] == "ok"
    background, owner = (r["headers"] for r in stub.chat_requests()[-2:])
    assert (background["poundlock-priority"], background["poundlock-wait"]) == ("background", "0")
    assert "poundlock-priority" not in owner and "poundlock-wait" not in owner


def test_after_a_busy_answer_every_background_task_waits_together(ws, openai_stub):
    stub = openai_stub(errors=[409])
    config = local(ws, stub)
    assert ask(config)["failure"] == "busy"
    before = len(stub.chat_requests())
    held = ask(config, task="life.group")
    assert held["status"] == "unavailable" and held["failure"] == "paced" and len(stub.chat_requests()) == before
    assert ask(config, task="life.group", background=False)["status"] == "ok"  # the owner's questions still go


def test_the_back_off_grows_with_each_failure_and_ends_with_an_answer():
    for n, expected in enumerate([60, 300, 900, 3600, 3600]):
        at = 10_000.0 * (n + 1)
        pacing.record("backoff", "ep", at, "busy" if n % 2 else "transient", clock=lambda at=at: at)
        assert pacing.wait("backoff", "ep", 300, clock=lambda at=at: at) == expected
    pacing.record("backoff", "ep", 99_000.0, None, clock=lambda: 99_001.0)
    assert pacing.wait("backoff", "ep", 300, clock=lambda: 99_001.0) == 0


def test_background_work_takes_at_most_a_quarter_of_any_hour():
    pacing.record("share", "ep", 0.0, None, clock=lambda: 400.0)  # 400 s on the model
    assert pacing.wait("share", "ep", 300, clock=lambda: 400.0) == 0  # a full 300 s call would still fit in 900 s
    pacing.record("share", "ep", 400.0, None, clock=lambda: 600.0)
    assert pacing.wait("share", "ep", 300, clock=lambda: 600.0) == 0
    pacing.record("share", "ep", 600.0, None, clock=lambda: 610.0)  # 610 s: one more could pass 900 s
    assert pacing.wait("share", "ep", 300, clock=lambda: 610.0) == 3390  # until the first call is an hour old
    assert pacing.wait("share", "ep", 300, clock=lambda: 4000.0) == 0


def test_months_are_grouped_only_after_that_years_reading_finishes(world):  # noqa: F811
    run = {"source_id": world.sid, "after": "2009-01-01", "before": "2009-12-31", "state": "running", "auto": True}
    with closing(open_store(world.store, "life", "worker")) as db:
        db.execute("INSERT INTO runs (run_id, source_id, slice, state, progress, started_at) VALUES (?,?,?,?,?,?)",
                   ("lrun_2009", world.sid, json.dumps(run), "running", "{}", "2026-10-24T00:00:00+00:00"))
        db.commit()
    life_groups.propose_groups(world.worker())
    asked = [p for p in world.model_prompts() if "see their life as memories" in p]
    assert asked and not any("2009-05" in p for p in asked)  # May 2009 is still being read
    assert "Trip to Example Junction" not in {g["title"] for g in life_groups.groups(world.store)}
    with closing(open_store(world.store, "life", "worker")) as db:
        db.execute("UPDATE runs SET state = 'done' WHERE run_id = 'lrun_2009'")
        db.commit()
    life_groups.propose_groups(world.worker())
    assert "Trip to Example Junction" in {g["title"] for g in life_groups.groups(world.store)}
