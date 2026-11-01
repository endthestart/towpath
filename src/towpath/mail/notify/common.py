"""The owner's settings for alerts, digests, follow-ups and reply labels, and proposing a change from them."""

import json
from contextlib import closing
from datetime import datetime, timezone

from towpath.canonical import digest
from towpath.mail import actions, settings
from towpath.stores import open_store

ROLE = "worker"
ALERT_MODES = {"off": "Off", "flag": "Flag the message", "keyword": "Label it “Important”"}
DIGEST_EVERY = {"off": "Off", "daily": "Every morning", "weekly": "Monday mornings"}
FOLLOW_UP_DAYS = {0: "Off", 3: "After 3 days", 7: "After a week", 14: "After two weeks"}
FOLLOW_UP_LABEL = "Follow-up"
FOLLOW_UP_WINDOW_DAYS = 30
REPLY_LABELS = {"needs_owner_reply": "To-reply", "awaiting_others": "Awaiting-reply"}
REPLY_LABELS_WINDOW_DAYS = 30
TIMEZONE = "America/Chicago"
DIGEST_HOUR = 7


def now() -> datetime:
    return datetime.now(timezone.utc)


# -- the owner's settings (web role) ------------------------------------------------------------------------------

def alert_setting(store_dir, source_id: str, role: str = ROLE) -> dict:
    return settings._get(store_dir, source_id, "alerts", role) or {"mode": "off"}


def set_alerts(store_dir, source_id: str, mode: str, author: str = "owner") -> None:
    if mode not in ALERT_MODES:
        raise ValueError("unknown alert mode")
    settings._set(store_dir, source_id, "alerts", {"mode": mode, "since": now().isoformat(timespec="seconds")},
                  "mail-alerts", author)


def digest_setting(store_dir, source_id: str, role: str = ROLE) -> dict:
    return settings._get(store_dir, source_id, "digest", role) or {"every": "off"}


def set_digest(store_dir, source_id: str, every: str, author: str = "owner") -> None:
    if every not in DIGEST_EVERY:
        raise ValueError("unknown digest frequency")
    settings._set(store_dir, source_id, "digest", {"every": every, "since": now().isoformat(timespec="seconds")},
                  "mail-digest", author)


def follow_up_days(store_dir, source_id: str, role: str = ROLE) -> int:
    return int((settings._get(store_dir, source_id, "follow_up", role) or {}).get("days", 0))


def set_follow_up(store_dir, source_id: str, days: int, author: str = "owner") -> None:
    if days not in FOLLOW_UP_DAYS:
        raise ValueError("unknown follow-up delay")
    settings._set(store_dir, source_id, "follow_up", {"days": days}, "mail-follow-up", author)


def reply_labels_setting(store_dir, source_id: str, role: str = ROLE) -> dict:
    return settings._get(store_dir, source_id, "reply_labels", role) or {"on": False}


def set_reply_labels(store_dir, source_id: str, on: bool, author: str = "owner") -> None:
    settings._set(store_dir, source_id, "reply_labels", {"on": bool(on), "since": now().isoformat(timespec="seconds")},
                  "mail-reply-labels", author)


def auto_approved(store_dir, body: dict, role: str = "act", cache: dict | None = None) -> bool:
    """An alert, digest or follow-up label the owner's current setting asked for. ``cache`` keeps the settings read
    for one pass over many proposals."""
    producer = str(body.get("producer", ""))
    source = body.get("source_id")
    if not producer.startswith(("alert:", "digest:", "follow-up:", "reply-labels:")):
        return False
    memo = cache.setdefault("notify", {}) if cache is not None else {}

    def read(fn):
        if (fn, source) not in memo:
            memo[(fn, source)] = fn(store_dir, source, role)
        return memo[(fn, source)]

    keyword = body.get("params", {}).get("keyword")
    if producer == f"reply-labels:{source}" and keyword in REPLY_LABELS.values():
        # Taking off a label Towpath put on is always approved; putting one on only while the setting is on.
        return body.get("type") == "remove_keyword" or (
            body.get("type") == "add_keyword" and read(reply_labels_setting)["on"])
    if producer == f"follow-up:{source}" and keyword == FOLLOW_UP_LABEL:
        # Taking the label off is always approved; putting it on only while reminders are on.
        return body.get("type") == "remove_keyword" or (
            body.get("type") == "add_keyword" and read(follow_up_days) > 0)
    if producer == f"alert:{source}":
        mode = read(alert_setting)["mode"]
        return (mode, body.get("type")) in (("flag", "flag"), ("keyword", "add_keyword"))
    if producer.startswith(f"digest:{source}:"):
        return body.get("type") == "inbox_note" and read(digest_setting)["every"] != "off"
    return False


def _propose(config, body: dict) -> str:
    pid = actions.proposal_id(body)
    with closing(open_store(config.store_dir, "mailplan", ROLE)) as plan:
        plan.execute("INSERT OR IGNORE INTO proposals (proposal_id, source_id, body, digest, created_at) "
                     "VALUES (?,?,?,?,?)", (pid, body["source_id"], json.dumps(body, sort_keys=True), digest(body),
                                            now().isoformat(timespec="seconds")))
        plan.commit()
    return pid
