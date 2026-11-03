"""One bounded run of background model work: for a fixed time it may use the model fully, then that ends by itself.

The owner starts it once (a decision the web role writes). Nothing reopens it: when the time is up, or the owner
stops it, background work is back under its usual share of the model.
"""

import json
from contextlib import closing
from datetime import datetime, timedelta, timezone

from towpath.stores import open_store, store_path

TARGET, KEY = "life:run-once", "window"
HOURS = 48


def _now() -> datetime:
    return datetime.now(timezone.utc)


def window(store_dir, role: str = "worker") -> dict | None:
    """The run the owner started, open or over, if any."""
    if not store_path(store_dir, "decisions").exists():
        return None
    with closing(open_store(store_dir, "decisions", role)) as db:
        row = db.execute("SELECT value FROM settings WHERE target_id = ? AND key = ?", (TARGET, KEY)).fetchone()
    return json.loads(row[0]) if row else None


def is_open(store_dir, role: str = "worker") -> bool:
    value = window(store_dir, role)
    return value is not None and _now() < datetime.fromisoformat(value["until"])


def ended(store_dir, role: str = "worker") -> bool:
    return window(store_dir, role) is not None and not is_open(store_dir, role)


def _write(store_dir, value: dict, author: str) -> None:
    at = _now().isoformat(timespec="seconds")
    with closing(open_store(store_dir, "decisions", "web")) as db:
        db.execute("INSERT OR REPLACE INTO settings (target_id, key, value, author, at) VALUES (?,?,?,?,?)",
                   (TARGET, KEY, json.dumps(value, sort_keys=True), author, at))
        # Logged as a change of the owner's request, so a run waiting out a retry goes on at once.
        db.execute("INSERT INTO decision_log (kind, target_id, detail, author, at) VALUES ('life-request', ?, ?, ?, ?)",
                   (TARGET, json.dumps(value, sort_keys=True), author, at))
        db.commit()


def start(store_dir, author: str = "owner") -> dict:
    """Open the window. ``life_propose.start_run`` is the way in: it refuses a run that has been used."""
    at = _now()
    value = {"started_at": at.isoformat(timespec="seconds"),
             "until": (at + timedelta(hours=HOURS)).isoformat(timespec="seconds")}
    _write(store_dir, value, author)
    return value


def stop(store_dir, author: str = "owner") -> None:
    if is_open(store_dir, "web"):
        _write(store_dir, {**window(store_dir, "web"), "until": _now().isoformat(timespec="seconds"), "stopped": True},
               author)
