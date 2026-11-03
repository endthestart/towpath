"""The Life page and its settings: the account, the model, the owner's profile, and starting or pausing."""

from django.conf import settings
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST

from datetime import date

from towpath import decisions, life, life_groups, life_propose, stores
from towpath.unified import grants
from towpath.web.life_views.common import FEATURE, LIFE_STORES, SHOW, TASK, _accounts, _config, _row, _shown, _status


@require_GET
def overview(request):
    stores.require_current(settings.TOWPATH_STORE_DIR, LIFE_STORES)
    config = _config()
    endpoint_id = config.tasks.get(TASK)
    endpoint = config.endpoints.get(endpoint_id) if endpoint_id else None
    proposed = life.claims(settings.TOWPATH_STORE_DIR, limit=5000)
    show = request.GET.get("show", "review")
    show = show if show in SHOW else "review"
    rows = [_row(c) for c in proposed if _shown(c, show)][:300]
    status = _status()
    year = date.today().year - 1
    from towpath import life_conflicts, life_evidence

    return render(request, "life.html", {
        "pairs": len(life_conflicts.find(settings.TOWPATH_STORE_DIR)),
        "evidence": {"on": life_evidence.enabled(settings.TOWPATH_STORE_DIR, "web"),
                     "kept": len(life_evidence.kept(settings.TOWPATH_STORE_DIR))},
        "nav": "life", "profile": life_groups.profile(settings.TOWPATH_STORE_DIR), "accounts": _accounts(config),
        "saved": request.GET.get("saved"), "show": show,
        "model": {"id": endpoint_id, "name": endpoint.model, "address": endpoint.base_url,
                  "allowed": decisions.model_granted(config, endpoint_id, "content")} if endpoint else None,
        "rows": rows, "counts": {**{s: sum(c.state == s for c in proposed) for s in ("accepted", "corrected",
                                                                                   "rejected")},
                                 "proposed": sum(_shown(c, "review") for c in proposed),
                                 "minor": sum(_shown(c, "minor") for c in proposed)},
        "status": status, "live": status["running"],
        "default_after": (status["request"] or {}).get("after", f"{year}-01-01"),
        "default_before": (status["request"] or {}).get("before", f"{year}-12-31"),
    })


@require_GET
def status(request):
    """The proposal panel alone, polled while proposals are being made."""
    return render(request, "_life_status.html", {"status": _status()})


@require_POST
def start(request):
    sid = request.POST.get("source_id", "")
    allowed = [a["source_id"] for a in _accounts(_config()) if a["allowed"]]
    if sid not in allowed:
        return HttpResponseBadRequest("Choose a mail account you allowed above.")
    try:
        life_propose.set_request(settings.TOWPATH_STORE_DIR, sid, request.POST.get("after", ""),
                                 request.POST.get("before", ""))
    except ValueError:
        return HttpResponseBadRequest("Choose a period with a start date before its end date.")
    return redirect("/life/?saved=started")


@require_POST
def profile(request):
    try:
        life_groups.set_profile(settings.TOWPATH_STORE_DIR, request.POST.get("name", ""),
                                request.POST.get("birthday") or None)
    except ValueError:
        return HttpResponseBadRequest("Enter the birthday as a date.")
    return redirect("/life/?saved=profile")


@require_POST
def auto(request):
    """Turn finding events by itself on or off."""
    life_propose.set_auto(settings.TOWPATH_STORE_DIR, request.POST.get("on") == "1")
    return redirect("/life/?saved=started")


@require_POST
def run_once(request):
    """The owner's one bounded run; it can't be started a second time, or while nothing could be read."""
    if not life_propose.can_run(_config()):
        return HttpResponse("Allow a mail account and the AI model first.", status=409)
    if life_propose.start_run(settings.TOWPATH_STORE_DIR) is None:
        return HttpResponse("Run once has already been used.", status=409)
    return redirect("/life/?saved=started")


@require_POST
def pause(request):
    life_propose.pause(settings.TOWPATH_STORE_DIR, request.POST.get("pause") == "1")
    return redirect("/life/")


@require_POST
def account(request):
    config = _config()
    sid = request.POST.get("source_id", "")
    if sid not in {a["source_id"] for a in _accounts(config)}:
        return HttpResponseBadRequest("Choose one of your mail accounts.")
    if request.POST.get("allow") == "1":
        grants.grant(settings.TOWPATH_STORE_DIR, sid, FEATURE, author="owner")
    else:
        grants.revoke(settings.TOWPATH_STORE_DIR, sid, FEATURE, author="owner")
    return redirect("/life/?saved=accounts")


@require_POST
def model(request):
    config = _config()
    endpoint_id = config.tasks.get(TASK)
    if endpoint_id is None or endpoint_id != request.POST.get("endpoint_id"):
        return HttpResponseBadRequest("Connect an AI model on the Connections page first.")
    if request.POST.get("allow") == "1":
        decisions.grant_model(config, endpoint_id, "content", author="owner")
    else:
        decisions.revoke_model(config, endpoint_id, "content", author="owner")
    return redirect("/life/?saved=model")
