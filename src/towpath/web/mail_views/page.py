"""The Mail page and an account's settings: what may change mail, answers to proposals, and undoing."""

from django.http import HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST

from towpath import decisions, stores
from towpath.mail import FEATURE, actions, executor, notify, settings as mail_settings, threads
from towpath.unified import grants
from towpath.web.mail_views.common import (
    MAIL_STORES,
    MAIL_TASKS,
    _account,
    _accounts,
    _activity,
    _back,
    _config,
    _models,
    _proposal_groups,
    _proposals,
    _store,
    _undone,
)


@require_GET
def overview(request):
    stores.require_current(_store(), MAIL_STORES)
    config = _config()
    accounts = _accounts(config)
    types = [{"key": k, **v} for k, v in actions.TYPES.items()]
    return render(request, "mail.html", {"nav": "mail", "accounts": accounts, "types": types,
                                         "alert_modes": list(notify.ALERT_MODES.items()),
                                         "digest_every": list(notify.DIGEST_EVERY.items()),
                                         "follow_up_days": list(notify.FOLLOW_UP_DAYS.items()),
                                         "saved": request.GET.get("saved"), "groups": _proposal_groups(_proposals()),
                                         "models": _models(config), "labels": threads.LABELS, **_activity()})


@require_POST
def model(request):
    """Choose the model a mail feature uses, or allow or stop it reading email text."""
    config = _config()
    task, endpoint = request.POST.get("task", ""), request.POST.get("endpoint", "")
    if task not in MAIL_TASKS or endpoint not in config.endpoints:
        return HttpResponseBadRequest("Choose one of the listed models.")
    if "allow" in request.POST:
        if request.POST["allow"] == "1":
            decisions.grant_model(config, endpoint, "content", author="owner")
        else:
            decisions.revoke_model(config, endpoint, "content", author="owner")
    else:
        decisions.set_task_model(_store(), task, endpoint, author="owner")
    return redirect("/mail/?saved=models#models")


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
    return _back(request, "/mail/?saved=paused")


@require_POST
def writes(request):
    sid = request.POST.get("source_id", "")
    if not _account(_config(), sid)["managed"]:
        return HttpResponseBadRequest("Turn mail management on for this account first.")
    chosen = set(request.POST.getlist("types")) & set(actions.TYPES)
    mail_settings.set_enabled_types(_store(), sid, chosen)
    return _back(request, "/mail/?saved=writes")


@require_POST
def dry_run(request):
    sid = request.POST.get("source_id", "")
    if not _account(_config(), sid)["managed"]:
        return HttpResponseBadRequest("Turn mail management on for this account first.")
    mail_settings.set_dry_run(_store(), sid, request.POST.get("dry_run") == "1")
    return _back(request, "/mail/?saved=dry-run")


@require_GET
def settings_page(request, sid):
    account = _account(_config(), sid)
    if not account["managed"]:
        return redirect("/mail/")
    account = next(a for a in _accounts(_config()) if a["source_id"] == sid)
    return render(request, "mail_settings.html", {
        "nav": "mail", "a": account, "here": f"/mail/{sid}/settings", "saved": request.GET.get("saved"),
        "types": [{"key": k, **v} for k, v in actions.TYPES.items()],
        "alert_modes": list(notify.ALERT_MODES.items()), "digest_every": list(notify.DIGEST_EVERY.items()),
        "follow_up_days": list(notify.FOLLOW_UP_DAYS.items())})


@require_POST
def tell_me(request):
    """Important-mail alerts and the digest for one account."""
    sid = request.POST.get("source_id", "")
    if not _account(_config(), sid)["managed"]:
        return HttpResponseBadRequest("Turn mail management on for this account first.")
    try:
        if "alerts" in request.POST:
            notify.set_alerts(_store(), sid, request.POST["alerts"])
        if "digest" in request.POST:
            notify.set_digest(_store(), sid, request.POST["digest"])
        if "follow_up" in request.POST:
            notify.set_follow_up(_store(), sid, int(request.POST["follow_up"]))
        if "reply_labels" in request.POST and request.POST["reply_labels"] in ("0", "1"):
            on = request.POST["reply_labels"] == "1"
            if on != notify.reply_labels_setting(_store(), sid, "web")["on"]:
                notify.set_reply_labels(_store(), sid, on)
    except ValueError:
        return HttpResponseBadRequest("Choose one of the listed options.")
    return _back(request, "/mail/?saved=1")


@require_POST
def draft_notes(request):
    """What drafts may use: availability, phone number, sign-off, style."""
    sid = request.POST.get("source_id", "")
    if not _account(_config(), sid)["managed"]:
        return HttpResponseBadRequest("Turn mail management on for this account first.")
    try:
        threads.set_notes(_store(), sid, request.POST.get("notes", ""))
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc).capitalize() + ".")
    return _back(request, "/mail/?saved=1")


@require_GET
def stats_page(request, sid):
    account = _account(_config(), sid)
    if not account["managed"]:
        return redirect("/mail/")
    found = notify.stats(_store(), sid)
    peak = max((w[1] + w[2] for w in (found or {}).get("weeks", [])), default=0)
    return render(request, "mail_stats.html", {"nav": "mail", "account": account, "stats": found, "peak": peak})


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
def answer_batch(request):
    """Approve or reject every waiting proposal from one rule version at once."""
    producer = request.POST.get("producer", "")
    found = [p for p in _proposals() if (p["body"].get("producer") or "other") == producer]
    if not found:
        return HttpResponseBadRequest("Nothing from that rule is waiting.")
    decision = "approved" if request.POST.get("approve") == "1" else "rejected"
    for p in found:
        mail_settings.answer(_store(), p["proposal_id"], p["digest"], decision)
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
    return _back(request, "/mail/?saved=undo")
