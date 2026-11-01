"""What the owner asked for (a period, or finding by itself), whether it can run, and how far it has got."""

import json
from contextlib import closing
from datetime import date

from towpath.canonical import digest
from towpath.life_propose.common import AUTO_KEY, AUTO_TARGET, PROMPT_VERSION, REQUEST_KEY, REQUEST_TARGET, TASK
from towpath.stores import open_store, store_path


def request(store_dir) -> dict | None:
    """The slice the owner asked for on the Life page, if any."""
    if not store_path(store_dir, "decisions").exists():
        return None
    with closing(open_store(store_dir, "decisions", "worker")) as db:
        row = db.execute("SELECT value FROM settings WHERE target_id = ? AND key = ?",
                         (REQUEST_TARGET, REQUEST_KEY)).fetchone()
    return json.loads(row[0]) if row else None


def _requested_at(store_dir) -> str:
    """Which change of the owner's request is current: the decision log's sequence, which only grows."""
    with closing(open_store(store_dir, "decisions", "worker")) as db:
        row = db.execute("SELECT max(seq) FROM decision_log WHERE kind = 'life-request'").fetchone()
    return str(row[0] or 0)


def set_request(store_dir, source_id: str, after: str, before: str, author: str = "owner") -> dict:
    """The owner's choice of slice (web role). Changing it starts a new run; the claims already proposed stay."""
    for value in (after, before):
        date.fromisoformat(value)
    if after > before:
        raise ValueError("the period ends before it starts")
    value = {"source_id": source_id, "after": after, "before": before, "state": "running"}
    _write(store_dir, value, author)
    return value


def pause(store_dir, paused: bool, author: str = "owner") -> None:
    value = request(store_dir)
    if value is not None:
        _write(store_dir, {**value, "state": "paused" if paused else "running"}, author)


def _write(store_dir, value: dict, author: str) -> None:
    from towpath.decisions import now

    with closing(open_store(store_dir, "decisions", "web")) as db:
        db.execute("INSERT OR REPLACE INTO settings (target_id, key, value, author, at) VALUES (?,?,?,?,?)",
                   (REQUEST_TARGET, REQUEST_KEY, json.dumps(value, sort_keys=True), author, now()))
        db.execute("INSERT INTO decision_log (kind, target_id, detail, author, at) VALUES ('life-request', ?, ?, ?, ?)",
                   (REQUEST_TARGET, json.dumps(value, sort_keys=True), author, now()))
        db.commit()


def auto(store_dir, role: str = "worker") -> bool:
    """Whether the owner turned on finding events by itself, across every account the life story may use."""
    if not store_path(store_dir, "decisions").exists():
        return False
    with closing(open_store(store_dir, "decisions", role)) as db:
        row = db.execute("SELECT value FROM settings WHERE target_id = ? AND key = ?",
                         (AUTO_TARGET, AUTO_KEY)).fetchone()
    return bool(row and json.loads(row[0]) == "running")


def set_auto(store_dir, on: bool, author: str = "owner") -> None:
    from towpath.decisions import now

    value = json.dumps("running" if on else "paused")
    with closing(open_store(store_dir, "decisions", "web")) as db:
        db.execute("INSERT OR REPLACE INTO settings (target_id, key, value, author, at) VALUES (?,?,?,?,?)",
                   (AUTO_TARGET, AUTO_KEY, value, author, now()))
        db.execute("INSERT INTO decision_log (kind, target_id, detail, author, at) VALUES ('life-request', ?, ?, ?, ?)",
                   (AUTO_TARGET, value, author, now()))
        db.commit()


def run_id(value: dict) -> str:
    """One run per slice and prompt version: a new prompt reads the slice again."""
    return "lrun_" + digest({**{k: value[k] for k in ("source_id", "after", "before")}, "prompt": PROMPT_VERSION})[:16]


def blocked(config, value: dict | None, role: str = "worker") -> str | None:
    """Why proposing can't run now, in plain words, or None."""
    from towpath import decisions
    from towpath.unified import grants

    if value is None:
        return "Turn on finding events, or choose a mail account and a period."
    if value.get("state") == "paused":
        return "Paused."
    if "life-evidence" not in grants.granted(config.store_dir).get(value["source_id"], set()):
        return "That account isn't allowed for your life story."
    endpoint_id = config.tasks.get(TASK)
    if endpoint_id is None:
        return "No AI model is connected."
    if not decisions.model_granted(config, endpoint_id, "content", role=role):
        return "The AI model isn't allowed to read email text yet."
    if value["source_id"] not in config.sources:
        return "That mail account isn't connected."
    return None


def progress(store_dir, role: str = "web") -> dict | None:
    if not store_path(store_dir, "life").exists():
        return None
    with closing(open_store(store_dir, "life", role)) as db:
        row = db.execute("SELECT run_id, state, progress, started_at, finished_at FROM runs ORDER BY started_at DESC "
                         "LIMIT 1").fetchone()
    return {**json.loads(row[2] or "{}"), "run_id": row[0], "state": row[1], "started_at": row[3],
            "finished_at": row[4]} if row else None


def auto_progress(store_dir, role: str = "web") -> dict:
    """Finding by itself: messages read and events proposed so far, the years finished, and the latest stop."""
    out = {"examined": 0, "proposed": 0, "years": [], "reason": None, "retry_at": None}
    if not store_path(store_dir, "life").exists():
        return out
    with closing(open_store(store_dir, "life", role)) as db:
        for slice_, state, raw in db.execute("SELECT slice, state, progress FROM runs ORDER BY started_at"):
            value, p = json.loads(slice_), json.loads(raw or "{}")
            if not value.get("auto"):
                continue
            out["examined"] += p.get("examined", 0)
            out["proposed"] += p.get("proposed", 0)
            if state == "done":
                out["years"].append(f"{value['source_id']} {value['after'][:4]}")
            if state == "stopped":
                out["reason"], out["retry_at"] = p.get("reason"), p.get("retry_at")
    return out
