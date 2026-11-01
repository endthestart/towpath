"""Reading mail: messages, the reader, conversations, and acting on them."""

import json
from contextlib import closing
from urllib.parse import quote

from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST

from towpath.mail import actions, senders, settings as mail_settings, threads
from towpath.stores import open_store, store_path
from towpath.web.mail_views.common import MESSAGE_ACTIONS, PAGE, _account, _activity, _back, _config, _store


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
    sender = (request.GET.get("from") or "").lower() or None
    category = request.GET.get("category") if request.GET.get("category") in senders.CATEGORIES else None
    before = request.GET.get("before") or "9999"
    rows = []
    boxes = []
    if store_path(_store(), "mail").exists():
        cats = senders.categories(_store(), sid) if category else {}
        with closing(open_store(_store(), "mail", "web")) as db:
            boxes = [r[0] for r in db.execute("SELECT DISTINCT mailbox FROM messages WHERE source_id = ? ORDER BY 1",
                                              (sid,))]
            where, args = ("from_addr = ?", sender) if sender else ("mailbox = ?", box)
            cursor = before
            for _ in range(40):  # a category is picked out of the folder a chunk at a time
                chunk = [dict(r) for r in db.execute(
                    "SELECT item_id, subject, from_name, from_addr, date_utc, flags, has_attachments, "
                    f"list_unsubscribe, list_id, mailbox FROM messages WHERE source_id = ? AND {where} AND "
                    "coalesce(date_utc, '') < ? ORDER BY date_utc DESC LIMIT ?",
                    (sid, args, cursor, (PAGE + 1) if not category else 500))]
                rows += [r for r in chunk if not category or
                         senders.message_category(r, cats.get(r["from_addr"])) == category]
                if not category or len(rows) > PAGE or len(chunk) < 500:
                    break
                cursor = chunk[-1]["date_utc"] or ""
    present = _present([r["item_id"] for r in rows])
    rows = [r for r in rows if r["item_id"] in present]
    more = len(rows) > PAGE
    rows = rows[:PAGE]
    for r in rows:
        r["unread"] = "\\Seen" not in json.loads(r["flags"])
    return render(request, "mail_messages.html", {
        "nav": "mail", "account": account, "box": box, "boxes": boxes, "rows": rows, "sender": sender,
        "category": category, "category_label": senders.CATEGORIES.get(category),
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
    activity = _activity(m["source_id"], item_id)
    mine = [r for r in activity["receipts"] + activity["waiting"] if r["body"]["target"].get("item_id") == item_id]
    choices = [(k, actions.TYPES[k]["label"]) for k in MESSAGE_ACTIONS
               if not (k == "mark_read" and not m["unread"]) and not (k == "mark_unread" and m["unread"])
               and not (k == "flag" and m["flagged"]) and not (k == "unflag" and not m["flagged"])
               and not (k == "archive" and m["mailbox"].lower() == "archive")]
    conversation = threads.thread(_store(), m["source_id"], m["thread_key"])
    from towpath.mail import privacy

    kept = privacy.kept(_store(), [m["item_id"], privacy.sender_target(m["source_id"], m["from_addr"])])
    from towpath.mail import attachments

    files = attachments.of_message(_store(), m["source_id"], m["item_id"])
    return render(request, "mail_reader.html", {
        "attachments": files,
        "nav": "mail", "m": m, "account": account, "thread": thread if len(thread) > 1 else [],
        "conversation": {**conversation, "label": threads.LABELS[conversation["state"]]} if conversation else None,
        "kept_item": m["item_id"] in kept, "kept_sender": len(kept - {m["item_id"]}) > 0,
        "states": list(threads.LABELS.items()),
        "here": m["item_id"] in present, "choices": choices, "activity": mine, "saved": request.GET.get("saved")})


@require_GET
def conversations(request, sid):
    account = _account(_config(), sid)
    if not account["managed"]:
        return redirect("/mail/")
    state = request.GET.get("state") if request.GET.get("state") in threads.STATES else "needs_owner_reply"
    rows = threads.listing(_store(), sid, state, limit=101, before=request.GET.get("before"))
    more = len(rows) > 100
    rows = rows[:100]
    counts = threads.counts(_store(), sid)
    return render(request, "mail_conversations.html", {
        "nav": "mail", "account": account, "state": state, "title": threads.LABELS[state], "rows": rows,
        "tabs": [(k, v, counts.get(k, 0)) for k, v in threads.LABELS.items()],
        "next_before": rows[-1]["last_date"] if more and rows else None, "saved": request.GET.get("saved")})


@require_GET
def conversation(request, sid):
    """A whole conversation, oldest first, with its state, the owner's mark and the draft."""
    account = _account(_config(), sid)
    if not account["managed"]:
        return redirect("/mail/")
    key = request.GET.get("t", "")
    found = threads.thread(_store(), sid, key)
    if found is None:
        raise Http404
    with closing(open_store(_store(), "mail", "web")) as db:
        msgs = threads._messages(db, sid, key)
        me = threads.owner(_store(), sid, db, "web")
    for m in msgs:
        m["mine"] = m["from_addr"] in me["addresses"] or m["mailbox"] in me["sent"]
    return render(request, "mail_conversation.html", {
        "nav": "mail", "account": account, "c": {**found, "label": threads.LABELS[found["state"]]}, "msgs": msgs,
        "states": list(threads.LABELS.items()), "here": f"/mail/{sid}/conversation?t={quote(key)}",
        "saved": request.GET.get("saved")})


@require_POST
def mark(request):
    sid, key = request.POST.get("source_id", ""), request.POST.get("thread_key", "")
    if not _account(_config(), sid)["managed"] or threads.thread(_store(), sid, key) is None:
        return HttpResponseBadRequest("That conversation isn't known.")
    state = request.POST.get("state") or None
    try:
        threads.mark(_store(), sid, key, state)
    except ValueError:
        return HttpResponseBadRequest("Choose one of the listed states.")
    return _back(request, f"/mail/{sid}/conversations?saved=marked")


@require_POST
def save_draft(request):
    """Save a reply draft (as written by the model, or as the owner edited it) to the mailbox's Drafts folder."""
    sid, key = request.POST.get("source_id", ""), request.POST.get("thread_key", "")
    if not _account(_config(), sid)["managed"]:
        return HttpResponseBadRequest("Turn mail management on for this account first.")
    found = threads.thread(_store(), sid, key)
    body = (request.POST.get("body") or (found or {}).get("draft") or "").strip()
    if found is None or not body:
        return HttpResponseBadRequest("There is no draft to save.")
    try:
        raw = threads.draft_message(_store(), sid, key, body)
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))
    mail_settings.ask(_store(), sid, actions.make(sid, "draft", {"mailbox_role": "drafts"}, {"message": raw},
                                                  reason=f"Reply to: {found['subject'] or '(no subject)'}"))
    return _back(request, f"/mail/{sid}/conversations?saved=draft")


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
