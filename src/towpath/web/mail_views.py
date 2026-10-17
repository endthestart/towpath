"""The Mail page: which accounts Towpath manages, what may change mail, what Towpath did, and a reader.

Everything here is the owner's choice or a record of what happened. Changes the owner asks for on a message
are recorded in the maildecisions store and carried out by the action runner in the connector. Until the owner
turns a kind of change on for an account (and adds its write password on the Connections page), each one is a
dry run.
"""

import json
from contextlib import closing

from django.conf import settings
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST

from towpath import connections, stores
from towpath.mail import FEATURE, actions, analysis, executor, settings as mail_settings, watch
from towpath.stores import open_store, store_path
from towpath.unified import grants

MAIL_STORES = ("decisions", "connections")
PAGE = 100
MESSAGE_ACTIONS = ("mark_read", "mark_unread", "flag", "unflag", "archive", "trash")
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
            "progress": analysis.progress(_store(), sid), "watch": watch.state(_store(), sid)})
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


def _activity(source_id: str | None = None) -> dict:
    rows = executor.receipts(_store(), source_id, limit=50)
    waiting = [w for w in executor.pending(_store(), role="web") if source_id in (None, w["source_id"])]
    undone = _undone(rows, waiting)
    names = _subjects([b["body"]["target"]["item_id"] for b in rows + waiting if b["body"]["target"].get("item_id")])
    for r in rows + waiting:
        target = r["body"]["target"]
        r["what"] = actions.describe(r["body"])
        r["message"] = names.get(target.get("item_id")) or {"subject": target.get("message_id") or "a new message"}
        r["outcome_text"] = OUTCOMES.get(r.get("outcome"), "Waiting")
        r["can_undo"] = r.get("outcome") == "done" and r.get("inverse") and r["key"] not in undone
    return {"receipts": rows, "waiting": waiting}


def _proposals() -> list[dict]:
    """Proposals that have no answer yet (none are made before the features of later milestones)."""
    if not store_path(_store(), "mailplan").exists():
        return []
    answered = mail_settings.answers(_store(), role="web")
    with closing(open_store(_store(), "mailplan", "web")) as db:
        rows = [dict(r) for r in db.execute("SELECT * FROM proposals ORDER BY created_at DESC LIMIT 200")]
    out = []
    for r in rows:
        if r["proposal_id"] in answered:
            continue
        r["body"] = json.loads(r["body"])
        r["what"] = actions.describe(r["body"])
        out.append(r)
    return out


@require_GET
def overview(request):
    stores.require_current(_store(), MAIL_STORES)
    config = _config()
    accounts = _accounts(config)
    types = [{"key": k, **v} for k, v in actions.TYPES.items()]
    return render(request, "mail.html", {"nav": "mail", "accounts": accounts, "types": types,
                                         "saved": request.GET.get("saved"), "proposals": _proposals(),
                                         **_activity()})


def _account(config, sid: str) -> dict:
    found = next((a for a in _accounts(config) if a["source_id"] == sid), None)
    if found is None:
        raise Http404
    return found


@require_POST
def manage(request):
    config = _config()
    sid = request.POST.get("source_id", "")
    account = next((a for a in _accounts(config) if a["source_id"] == sid), None)
    if account is None or not account["supported"]:
        return HttpResponseBadRequest("Choose one of your email accounts reached over IMAP.")
    if request.POST.get("manage") == "1":
        grants.grant(_store(), sid, FEATURE, author="owner")
    else:
        grants.revoke(_store(), sid, FEATURE, author="owner")
    return redirect("/mail/?saved=accounts")


@require_POST
def pause(request):
    sid = request.POST.get("source_id", "")
    _account(_config(), sid)
    mail_settings.set_paused(_store(), sid, request.POST.get("pause") == "1")
    return redirect("/mail/?saved=paused")


@require_POST
def writes(request):
    sid = request.POST.get("source_id", "")
    if not _account(_config(), sid)["managed"]:
        return HttpResponseBadRequest("Turn mail management on for this account first.")
    chosen = set(request.POST.getlist("types")) & set(actions.TYPES)
    mail_settings.set_enabled_types(_store(), sid, chosen)
    return redirect("/mail/?saved=writes")


@require_POST
def dry_run(request):
    sid = request.POST.get("source_id", "")
    if not _account(_config(), sid)["managed"]:
        return HttpResponseBadRequest("Turn mail management on for this account first.")
    mail_settings.set_dry_run(_store(), sid, request.POST.get("dry_run") == "1")
    return redirect("/mail/?saved=dry-run")


@require_POST
def answer(request):
    pid = request.POST.get("proposal_id", "")
    found = next((p for p in _proposals() if p["proposal_id"] == pid), None)
    if found is None:
        return HttpResponseBadRequest("That proposal no longer waits for an answer.")
    mail_settings.answer(_store(), pid, found["digest"], "approved" if request.POST.get("approve") == "1"
                         else "rejected")
    return redirect("/mail/?saved=answer")


@require_POST
def undo(request):
    key = request.POST.get("key", "")
    rows = executor.receipts(_store(), limit=500)
    found = next((r for r in rows if r["key"] == key), None)
    waiting = executor.pending(_store(), role="web")
    if found is None or found["outcome"] != "done" or not found["inverse"] or key in _undone(rows, waiting):
        return HttpResponseBadRequest("That change can't be undone from here.")
    mail_settings.ask(_store(), found["source_id"], found["inverse"])
    return redirect(request.POST.get("next") or "/mail/?saved=undo")


# -- messages and the reader ------------------------------------------------------------------------------------

def _message(item_id: str) -> dict:
    if not store_path(_store(), "mail").exists():
        raise Http404
    with closing(open_store(_store(), "mail", "web")) as db:
        row = db.execute("SELECT * FROM messages WHERE item_id = ?", (item_id,)).fetchone()
    if row is None:
        raise Http404
    m = dict(row)
    for k in ("flags", "to_addrs", "cc_addrs", "refs", "problems"):
        m[k] = json.loads(m[k])
    m["unread"] = "\\Seen" not in m["flags"]
    m["flagged"] = "\\Flagged" in m["flags"]
    return m


def _present(item_ids: list[str]) -> set[str]:
    """Which messages the index still finds where Towpath read them (a moved message has a new identity)."""
    if not item_ids:
        return set()
    marks = ",".join("?" for _ in item_ids)
    with closing(open_store(_store(), "source", "web")) as db:
        return {r[0] for r in db.execute(f"SELECT item_id FROM items WHERE absent_since_run IS NULL AND item_id IN "
                                         f"({marks})", item_ids)}


@require_GET
def messages(request, sid):
    config = _config()
    account = _account(config, sid)
    if not account["managed"]:
        return redirect("/mail/")
    box = request.GET.get("box") or "INBOX"
    before = request.GET.get("before") or "9999"
    rows = []
    boxes = []
    if store_path(_store(), "mail").exists():
        with closing(open_store(_store(), "mail", "web")) as db:
            boxes = [r[0] for r in db.execute("SELECT DISTINCT mailbox FROM messages WHERE source_id = ? ORDER BY 1",
                                              (sid,))]
            rows = [dict(r) for r in db.execute(
                "SELECT item_id, subject, from_name, from_addr, date_utc, flags, has_attachments, list_unsubscribe "
                "FROM messages WHERE source_id = ? AND mailbox = ? AND coalesce(date_utc, '') < ? "
                "ORDER BY date_utc DESC LIMIT ?", (sid, box, before, PAGE + 1))]
    present = _present([r["item_id"] for r in rows])
    rows = [r for r in rows if r["item_id"] in present]
    more = len(rows) > PAGE
    rows = rows[:PAGE]
    for r in rows:
        r["unread"] = "\\Seen" not in json.loads(r["flags"])
    return render(request, "mail_messages.html", {
        "nav": "mail", "account": account, "box": box, "boxes": boxes, "rows": rows,
        "next_before": rows[-1]["date_utc"] if more and rows else None})


@require_GET
def reader(request, item_id):
    m = _message(item_id)
    config = _config()
    account = _account(config, m["source_id"])
    with closing(open_store(_store(), "mail", "web")) as db:
        thread = [dict(r) for r in db.execute(
            "SELECT item_id, subject, from_name, from_addr, date_utc, mailbox FROM messages WHERE source_id = ? AND "
            "thread_key = ? ORDER BY date_utc", (m["source_id"], m["thread_key"]))]
    present = _present([m["item_id"]])
    activity = _activity(m["source_id"])
    mine = [r for r in activity["receipts"] + activity["waiting"] if r["body"]["target"].get("item_id") == item_id]
    choices = [(k, actions.TYPES[k]["label"]) for k in MESSAGE_ACTIONS
               if not (k == "mark_read" and not m["unread"]) and not (k == "mark_unread" and m["unread"])
               and not (k == "flag" and m["flagged"]) and not (k == "unflag" and not m["flagged"])
               and not (k == "archive" and m["mailbox"].lower() == "archive")]
    return render(request, "mail_reader.html", {
        "nav": "mail", "m": m, "account": account, "thread": thread if len(thread) > 1 else [],
        "here": m["item_id"] in present, "choices": choices, "activity": mine, "saved": request.GET.get("saved")})


@require_POST
def act(request):
    item_id = request.POST.get("item_id", "")
    kind = request.POST.get("type", "")
    if kind not in MESSAGE_ACTIONS:
        return HttpResponseBadRequest("Choose one of the listed changes.")
    m = _message(item_id)
    if not _account(_config(), m["source_id"])["managed"]:
        return HttpResponseBadRequest("Turn mail management on for this account first.")
    body = actions.make(m["source_id"], kind, actions.message_target(m), reason="Asked for on the message",
                        producer="owner")
    mail_settings.ask(_store(), m["source_id"], body)
    return redirect(f"/mail/m/{item_id}/?saved=asked")
