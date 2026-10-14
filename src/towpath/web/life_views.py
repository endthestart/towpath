"""The Life page: the life story's settings now, and the claims proposed for review as they arrive.

The owner chooses which mail accounts the life story may use and whether the text of that mail may be sent to the
AI model connected on the Connections page. Both are decisions in the decisions store; nothing is proposed until
both are given.
"""

from django.conf import settings
from django.http import HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST

from towpath import connections, decisions, life, stores
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


@require_GET
def overview(request):
    stores.require_current(settings.TOWPATH_STORE_DIR, LIFE_STORES)
    config = _config()
    endpoint_id = config.tasks.get(TASK)
    endpoint = config.endpoints.get(endpoint_id) if endpoint_id else None
    proposed = life.claims(settings.TOWPATH_STORE_DIR)
    return render(request, "life.html", {
        "nav": "life", "accounts": _accounts(config), "saved": request.GET.get("saved"),
        "model": {"id": endpoint_id, "name": endpoint.model, "address": endpoint.base_url,
                  "allowed": decisions.model_granted(config, endpoint_id, "content")} if endpoint else None,
        "claims": proposed, "counts": {s: sum(c.state == s for c in proposed)
                                       for s in ("proposed", "accepted", "corrected", "rejected")},
    })


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
