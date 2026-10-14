"""The Life page: the life story's settings now, and the claims proposed for review as they arrive.

The owner chooses which mail accounts the life story may use and whether the text of that mail may be sent to the
AI model connected on the Connections page. Both are decisions in the decisions store; nothing is proposed until
both are given.
"""

from django.conf import settings
from django.http import HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST

from datetime import date
from urllib.parse import urlencode

from towpath import connections, decisions, life, life_propose, stores
from towpath.unified import grants

LIFE_STORES = ("decisions", "connections")
FEATURE = "life-evidence"
TASK = "life.propose"


def _config():
    return connections.merged(settings.TOWPATH_CONFIG, None, role="web")


def _accounts(config) -> list[dict]:
    allowed = grants.granted(settings.TOWPATH_STORE_DIR)
    out = []
    for sid, source in config.sources.items():
        if source.kind == "mail-provider":
            out.append({"source_id": sid, "name": sid, "allowed": FEATURE in allowed.get(sid, set())})
    names = {c["source_id"]: c["display_name"] for c in connections.all_connections(settings.TOWPATH_STORE_DIR,
                                                                                    role="web")}
    for account in out:
        account["name"] = names.get(account["source_id"], account["name"])
    return out


SHOW = {"review": ("proposed",), "kept": ("accepted", "corrected"), "rejected": ("rejected",),
        "all": ("proposed", "accepted", "corrected", "rejected")}


def _status() -> dict:
    """The proposal job's state and why it can't run, for the page and its live panel."""
    config = _config()
    value = life_propose.request(settings.TOWPATH_STORE_DIR)
    run = life_propose.progress(settings.TOWPATH_STORE_DIR)
    if run is not None and value is not None and run["run_id"] != life_propose.run_id(value):
        run = None  # that run was for an earlier slice
    reason = life_propose.blocked(config, value)
    running = reason is None and not (run and run["state"] == "done")
    return {"request": value, "run": run, "blocked": reason, "running": running}


def _row(claim) -> dict:
    citation = claim.citations[0] if claim.citations else None
    return {"id": claim.claim_id, "statement": claim.statement, "when": claim.when.expression,
            "earliest": claim.when.earliest, "latest": claim.when.latest, "precision": claim.when.precision,
            "modality": {"plan": "planned", "occurred": "happened"}.get(claim.modality, claim.modality),
            "state": claim.state, "uncertainty": claim.uncertainty,
            "quote": citation.captured_excerpt if citation else None,
            "evidence_url": "/ref/?" + urlencode({"r": citation.ref}) if citation and citation.ref else None,
            "year": (claim.when.earliest or "")[:4] or "Undated"}


@require_GET
def overview(request):
    stores.require_current(settings.TOWPATH_STORE_DIR, LIFE_STORES)
    config = _config()
    endpoint_id = config.tasks.get(TASK)
    endpoint = config.endpoints.get(endpoint_id) if endpoint_id else None
    proposed = life.claims(settings.TOWPATH_STORE_DIR, limit=5000)
    show = request.GET.get("show", "review")
    wanted = SHOW.get(show, SHOW["review"])
    rows = [_row(c) for c in proposed if c.state in wanted][:300]
    status = _status()
    year = date.today().year - 1
    return render(request, "life.html", {
        "nav": "life", "accounts": _accounts(config), "saved": request.GET.get("saved"), "show": show,
        "model": {"id": endpoint_id, "name": endpoint.model, "address": endpoint.base_url,
                  "allowed": decisions.model_granted(config, endpoint_id, "content")} if endpoint else None,
        "rows": rows, "counts": {s: sum(c.state == s for c in proposed)
                                 for s in ("proposed", "accepted", "corrected", "rejected")},
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
def pause(request):
    life_propose.pause(settings.TOWPATH_STORE_DIR, request.POST.get("pause") == "1")
    return redirect("/life/")


@require_POST
def review(request):
    claim_id = request.POST.get("claim_id", "")
    action = request.POST.get("action", "")
    correction = None
    if action == "correct":
        correction = {"statement": request.POST.get("statement", "").strip()}
        earliest, latest = request.POST.get("earliest", "").strip(), request.POST.get("latest", "").strip()
        if earliest or latest:
            correction["when"] = {"expression": request.POST.get("expression", "").strip() or earliest,
                                  "precision": request.POST.get("precision", "day"),
                                  "earliest": earliest or latest, "latest": latest or earliest}
    if not any(c.claim_id == claim_id for c in life.claims(settings.TOWPATH_STORE_DIR, limit=5000)):
        return HttpResponseBadRequest("That proposal no longer exists.")
    try:
        life.review(settings.TOWPATH_STORE_DIR, claim_id, action, correction=correction)
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))
    return redirect(f"/life/?show={request.POST.get('show', 'review')}#claims")


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
