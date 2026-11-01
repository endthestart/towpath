"""What the Mail pages share: the accounts, the models, and what Towpath did and proposes."""

import json
from contextlib import closing

from django.conf import settings
from django.http import Http404
from django.shortcuts import redirect

from towpath import connections, decisions
from towpath.mail import FEATURE, actions, analysis, executor, notify, senders, settings as mail_settings, threads
from towpath.mail import watch
from towpath.stores import open_store, store_path
from towpath.unified import grants

MAIL_STORES = ("decisions", "connections")
PAGE = 100
MESSAGE_ACTIONS = ("mark_read", "mark_unread", "flag", "unflag", "archive", "trash")
WAITING_SHOWN = 30
OUTCOMES = {"done": "Done", "dry-run": "Dry run", "skipped": "Skipped", "refused": "Refused", "failed": "Failed"}


def _store():
    return settings.TOWPATH_STORE_DIR


def _config():
    return connections.merged(settings.TOWPATH_CONFIG, None, role="web")


def _accounts(config) -> list[dict]:
    allowed = grants.granted(_store())
    rows = {c["source_id"]: c for c in connections.all_connections(_store(), role="web")}
    out = []
    for sid, source in config.sources.items():
        if source.kind != "mail-provider":
            continue
        c = rows.get(sid) or {}
        managed = FEATURE in allowed.get(sid, set())
        out.append({
            "source_id": sid, "name": c.get("display_name") or sid,
            "address": (c.get("settings") or {}).get("username"), "adapter": source.adapter,
            "supported": source.adapter == "imap", "managed": managed,
            "write": (c.get("settings") or {}).get("write"),
            "enabled": mail_settings.enabled_types(_store(), sid, role="web"),
            "enabled_labels": [actions.TYPES[k]["label"] for k in actions.TYPES
                               if k in mail_settings.enabled_types(_store(), sid, role="web")],
            "paused": mail_settings.paused(_store(), sid, role="web"),
            "dry_run": mail_settings.dry_run(_store(), sid, role="web"),
            "progress": analysis.progress(_store(), sid), "watch": watch.state(_store(), sid),
            "conversations": threads.counts(_store(), sid) if managed else {},
            "inbox": senders.inbox(_store(), sid) if managed else None,
            "alerts": notify.alert_setting(_store(), sid, "web")["mode"],
            "digest": notify.digest_setting(_store(), sid, "web")["every"],
            "follow_up": notify.follow_up_days(_store(), sid, "web"),
            "reply_labels": notify.reply_labels_setting(_store(), sid, "web")["on"],
            "notes": threads.notes(_store(), sid, "web") if managed else ""})
    return out


MAIL_TASKS = ("mail.thread", "mail.draft", "mail.classify")


def _models(config) -> list[dict]:
    """Which model each mail feature uses, and whether that model may read email text."""
    model_ids = {c["source_id"] for c in connections.all_connections(_store(), role="web") if c["adapter"] == "model"}
    endpoints = [{"id": e.id, "name": e.model, "cloud": e.destination == "third-party"}
                 for e in config.endpoints.values() if e.id.split(":")[0] in model_ids]
    out = []
    for task in MAIL_TASKS:
        chosen = config.tasks.get(task)
        out.append({"task": task, "label": connections.TASK_LABELS[task], "chosen": chosen, "choices": endpoints,
                    "allowed": bool(chosen) and decisions.model_granted(config, chosen, "content")})
    return out


def _subjects(item_ids: list[str]) -> dict[str, dict]:
    if not item_ids or not store_path(_store(), "mail").exists():
        return {}
    marks = ",".join("?" for _ in item_ids)
    with closing(open_store(_store(), "mail", "web")) as db:
        return {r["item_id"]: dict(r) for r in db.execute(
            f"SELECT item_id, subject, from_addr, from_name FROM messages WHERE item_id IN ({marks})", item_ids)}


def _undone(rows: list[dict], waiting: list[dict]) -> set[str]:
    """Receipts whose change already has an Undo, carried out or waiting."""
    producers = {r["body"].get("producer") for r in rows} | {w["body"].get("producer") for w in waiting}
    return {r["key"] for r in rows if f"undo:{actions.proposal_id(r['body'])}" in producers}


def _activity(source_id: str | None = None, item_id: str | None = None) -> dict:
    rows = executor.receipts(_store(), source_id, limit=50 if item_id is None else 10**6)
    everything = [w for w in executor.pending(_store(), role="web") if source_id in (None, w["source_id"])]
    if item_id is not None:  # one message's changes, however many others are waiting
        rows = [r for r in rows if r["body"]["target"].get("item_id") == item_id]
        everything = [w for w in everything if w["body"]["target"].get("item_id") == item_id]
    undone = _undone(rows, everything)
    waiting = everything[:WAITING_SHOWN]
    names = _subjects([b["body"]["target"]["item_id"] for b in rows + waiting if b["body"]["target"].get("item_id")])
    for r in rows + waiting:
        target = r["body"]["target"]
        r["what"] = actions.describe(r["body"])
        r["message"] = names.get(target.get("item_id")) or {
            "subject": target.get("sender") or target.get("message_id") or "a new message"}
        r["outcome_text"] = OUTCOMES.get(r.get("outcome"), "Waiting")
        r["can_undo"] = r.get("outcome") == "done" and r.get("inverse") and r["key"] not in undone
    return {"receipts": rows, "waiting": waiting, "waiting_total": len(everything)}


def _proposals() -> list[dict]:
    """Proposals waiting for the owner: those without an answer that no automatic rule or archive request covers,
    newest first."""
    from towpath.mail import archive, notify, rules

    if not store_path(_store(), "mailplan").exists():
        return []
    answered = mail_settings.answers(_store(), role="web")
    with closing(open_store(_store(), "mailplan", "web")) as db:
        rows = [dict(r) for r in db.execute("SELECT * FROM proposals ORDER BY created_at DESC")]
    out = []
    cache: dict = {}
    for r in rows:
        if r["proposal_id"] in answered:
            continue
        r["body"] = json.loads(r["body"])
        if any(m.auto_approved(_store(), r["body"], role="web", cache=cache) for m in (rules, archive, notify)):
            continue
        r["what"] = actions.describe(r["body"])
        out.append(r)
    return out


def _proposal_groups(found: list[dict]) -> list[dict]:
    """Waiting proposals grouped by what made them (a rule's version), with a few examples each."""
    from towpath.mail import rules

    groups: dict[str, dict] = {}
    names = {f"rule:{r['rule_id']}@{r['version']}": r["body"]["name"] for r in rules.all_rules(_store(), role="web")}
    for p in found:
        key = p["body"].get("producer") or "other"
        g = groups.setdefault(key, {"producer": key, "name": names.get(key, key), "items": []})
        g["items"].append(p)
    subjects = _subjects([p["body"]["target"].get("item_id") for g in groups.values() for p in g["items"][:5]
                          if p["body"]["target"].get("item_id")])
    for g in groups.values():
        g["count"] = len(g["items"])
        g["examples"] = [{**p, "subject": (subjects.get(p["body"]["target"].get("item_id")) or {}).get("subject")}
                         for p in g["items"][:5]]
    return sorted(groups.values(), key=lambda g: -g["count"])


def _account(config, sid: str) -> dict:
    found = next((a for a in _accounts(config) if a["source_id"] == sid), None)
    if found is None:
        raise Http404
    return found


def _back(request, default: str):
    """Back to the Mail page a form came from (only pages under /mail/), else ``default``."""
    from towpath.web.auth import local_next

    return redirect(local_next(request, default))
