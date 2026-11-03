"""The owner's one bounded run: what was left when it started and what it did by the end, for the page and the log.

Counts and model names only, never anything from the mail: the worker's log carries these.
"""

import json
from contextlib import closing

from towpath import life
from towpath.life_propose.candidates import _candidates
from towpath.life_propose.choices import auto_progress, blocked, run_id
from towpath.life_propose.worker import _allowed, _year_slice, _years
from towpath.models import bulk
from towpath.stores import open_store, store_path

TASKS = ("life.propose", "life.group", "mail.thread", "mail.draft", "mail.classify")
EVERYTHING = 10 ** 9


def _emails_left(config) -> dict:
    emails = years = 0
    with closing(open_store(config.store_dir, "life", life.WRITER)) as db:
        done = {r[0] for r in db.execute("SELECT run_id FROM runs WHERE state = 'done'")}
        for sid in _allowed(config):
            for year in _years(config, sid):
                value = _year_slice(sid, year)
                rid = run_id(value)
                if rid in done:
                    continue
                read = {r[0] for r in db.execute("SELECT item_id FROM examined WHERE run_id = ?", (rid,))}
                found = sum(not m["advertising"] for m in _candidates(config, value, read, EVERYTHING))
                emails += found
                years += bool(found)
    return {"emails": emails, "years": years}


def _mail_left(config) -> dict:
    from towpath.mail import analysis, purchases, senders, threads

    out = {"conversations": 0, "drafts": 0, "senders": 0, "purchases": 0}
    if not store_path(config.store_dir, "mail").exists():
        return out
    for sid in analysis.eligible(config):
        with closing(open_store(config.store_dir, "mailintel", "worker")) as intel, \
                closing(open_store(config.store_dir, "mail", "worker")) as mail:
            out["conversations"] += threads.conversations_waiting(intel, sid)
            out["drafts"] += threads.drafts_waiting(config.store_dir, intel, sid)
            out["senders"] += senders.waiting(intel, sid)
            cats = senders.categories(config.store_dir, sid, "worker")
            out["purchases"] += purchases.waiting(mail, intel, sid, cats)
    return out


def _left(config) -> dict:
    """What finding events and the mail tasks still have to read, from the stores; no model is asked."""
    return {**_emails_left(config), **_mail_left(config)}


def _snapshot(config) -> dict:
    progress = auto_progress(config.store_dir, "worker")
    out = {"read": progress["examined"], "events": progress["proposed"],
           "oldest_year_finished": min((y.rsplit(" ", 1)[-1] for y in progress["years"]), default=None)}
    try:
        return {**out, "left": _left(config)}
    except Exception as exc:  # noqa: BLE001 - never stop the worker; keep data out of the reason
        return {**out, "left": None, "problem": f"Counting stopped on an unexpected error ({type(exc).__name__})."}


def can_run(config, role: str = "web") -> bool:
    """Whether finding events could read anything if the run started now, so its one use isn't spent on nothing."""
    return any(blocked(config, {"source_id": sid, "state": "running"}, role) is None for sid in _allowed(config))


def step(config) -> list[dict]:
    """The worker's part. Never raises: this unattended loop must outlast a locked store or a full disk."""
    try:
        return _record(config)
    except Exception as exc:  # noqa: BLE001 - never stop the worker; keep data out of the reason
        return [{"step": "run-once-problem", "error": type(exc).__name__}]


def _record(config) -> list[dict]:
    """Note the starting counts once the run is open and the result once it is over."""
    value = bulk.window(config.store_dir)
    if value is None:
        return []
    key = value["started_at"]
    with closing(open_store(config.store_dir, "life", life.WRITER)) as db:
        row = db.execute("SELECT at_start, finished_at FROM run_once WHERE started_at = ?", (key,)).fetchone()
    out = []
    if row is None:
        models = {t: config.endpoints[config.tasks[t]].model for t in TASKS
                  if config.tasks.get(t) in config.endpoints}
        at_start = {**_snapshot(config), "models": models}
        with closing(open_store(config.store_dir, "life", life.WRITER)) as db:
            db.execute("INSERT INTO run_once (started_at, at_start) VALUES (?,?)", (key, json.dumps(at_start)))
            db.commit()
        out.append({"step": "run-once-started", "until": value["until"], **at_start})
    else:
        if row["finished_at"]:
            return []
        at_start = json.loads(row["at_start"])
    if not bulk.is_open(config.store_dir):
        now = _snapshot(config)
        oldest = now["oldest_year_finished"]
        at_end = {**now, "read": now["read"] - at_start["read"], "events": now["events"] - at_start["events"],
                  "oldest_year_finished": oldest if oldest != at_start["oldest_year_finished"] else None}
        with closing(open_store(config.store_dir, "life", life.WRITER)) as db:
            db.execute("UPDATE run_once SET finished_at = ?, at_end = ? WHERE started_at = ?",
                       (life.now(), json.dumps(at_end), key))
            db.commit()
        out.append({"step": "run-once-finished", **at_end})
    return out


def summary(store_dir, role: str = "web") -> dict:
    """The run as the Life page shows it."""
    value = bulk.window(store_dir, role)
    if value is None:
        return {"state": "ready", "hours": bulk.HOURS}
    out = {"state": "on" if bulk.is_open(store_dir, role) else "ended", "until": value["until"],
           "stopped": value.get("stopped", False)}
    with closing(open_store(store_dir, "life", role)) as db:
        row = db.execute("SELECT at_start, at_end FROM run_once WHERE started_at = ?",
                         (value["started_at"],)).fetchone()
    if row is not None:
        out["at_start"] = json.loads(row["at_start"])
        out["at_end"] = json.loads(row["at_end"]) if row["at_end"] else None
    return out
