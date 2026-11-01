"""Deciding about claims: keeping evidence, sharing, remembering, forgetting and reviewing."""

from django.conf import settings
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import redirect
from django.views.decorators.http import require_GET, require_POST

from datetime import date

from towpath import life
from towpath.web.auth import local_next


@require_POST
def keep_evidence(request):
    from towpath import life_evidence

    life_evidence.set_enabled(settings.TOWPATH_STORE_DIR, request.POST.get("on") == "1", author="owner")
    return redirect("/life/?saved=evidence")


@require_GET
def evidence(request, claim_id):
    """The kept copy of an event's evidence, as a plain-text download (never rendered as a page)."""
    from django.http import HttpResponse

    from towpath import life_evidence

    data = life_evidence.copy(settings.TOWPATH_STORE_DIR, claim_id)
    if data is None:
        raise Http404
    response = HttpResponse(data, content_type="text/plain; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="evidence-{claim_id}.txt"'
    response["X-Content-Type-Options"] = "nosniff"
    return response


@require_POST
def share(request):
    """Mark an event or memory shareable for the family edition, or take it back."""
    from towpath import life_story

    try:
        life_story.share(settings.TOWPATH_STORE_DIR, request.POST.get("kind", ""), request.POST.get("key", ""),
                         request.POST.get("on") == "1", author="owner")
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))
    return redirect("/life/story?saved=1")


@require_POST
def remember(request):
    """Write down a memory in the owner's own words."""
    day = request.POST.get("date", "").strip()
    try:
        if len(day) == 4:
            expression, precision, earliest, latest = day, "year", f"{day}-01-01", f"{day}-12-31"
        elif len(day) == 7:
            import calendar

            y, m = int(day[:4]), int(day[5:7])
            expression, precision = day, "month"
            earliest, latest = f"{day}-01", f"{day}-{calendar.monthrange(y, m)[1]:02d}"
        else:
            date.fromisoformat(day)
            expression, precision, earliest, latest = day, "day", day, day
        people = [p for p in request.POST.get("people", "").split(",") if p.strip()]
        life.recollect(settings.TOWPATH_STORE_DIR, request.POST.get("statement", ""),
                       request.POST.get("expression", "").strip() or expression, precision, earliest, latest, people)
    except (ValueError, TypeError):
        return HttpResponseBadRequest("Write the memory, and when it was as a year (2009), a month (2009-05) or a "
                                      "day (2009-05-14).")
    return redirect("/life/?saved=memory")


@require_POST
def forget(request):
    life.forget(settings.TOWPATH_STORE_DIR, request.POST.get("claim_id", ""))
    return redirect(local_next(request, "/life/timeline"))


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
