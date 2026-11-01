"""Questions asked of the life story, and pairs of events that disagree."""

from django.conf import settings
from django.http import HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from urllib.parse import urlencode

from towpath import decisions, life, life_ask
from towpath.web.life_views.common import _config, _row


def _ask_view(q: dict) -> dict:
    a = q["answer"]
    if not a:
        return q
    sources = []
    for i, s in enumerate(a["sources"], start=1):
        url = (f"/mail/m/{s['item_id']}/" if s["kind"] in ("message", "purchase") else
               f"/life/year/{s['date']}" if s["kind"] == "year" else
               "/ref/?" + urlencode({"r": s["ref"]}) if s.get("ref") else None)
        sources.append({**s, "n": i, "url": url})
    return {**q, "sources": sources, "sentences": a["sentences"], "unknown": a["unknown"], "status": a["status"]}


@require_http_methods(["GET", "POST"])
def ask(request):
    store = settings.TOWPATH_STORE_DIR
    if request.method == "POST":
        try:
            life_ask.ask(store, request.POST.get("question", ""))
        except ValueError:
            return HttpResponseBadRequest("Ask a question.")
        return redirect("/life/ask")
    asked = [_ask_view(q) for q in life_ask.questions(store)]
    config = _config()
    endpoint_id = config.tasks.get("ask.answer")
    return render(request, "life_ask.html", {
        "nav": "life", "asked": asked, "waiting": any(q["answer"] is None for q in asked),
        "model_ok": bool(endpoint_id) and decisions.model_granted(config, endpoint_id, "content")})


@require_http_methods(["GET", "POST"])
def pairs(request):
    """Events that may disagree, for the owner to settle: keep one, or both are right."""
    from towpath import life_conflicts

    store = settings.TOWPATH_STORE_DIR
    if request.method == "POST":
        a, b, choice = request.POST.get("a", ""), request.POST.get("b", ""), request.POST.get("choice", "")
        known = {c.claim_id for c in life.claims(store, limit=10**6)}
        if a not in known or b not in known or choice not in ("a", "b", "both"):
            return HttpResponseBadRequest("Those events aren't known.")
        if choice == "both":
            life_conflicts.both_right(store, a, b)
        else:
            keep, drop = (a, b) if choice == "a" else (b, a)
            life.review(store, keep, "accept")
            life.review(store, drop, "reject")
        return redirect("/life/pairs?saved=1")
    found = life_conflicts.find(store)
    for p in found:
        p["a"], p["b"] = _row(p["a"]), _row(p["b"])
    return render(request, "life_pairs.html", {"nav": "life", "pairs": found, "saved": request.GET.get("saved")})
