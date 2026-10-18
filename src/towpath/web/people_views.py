"""People pages: everyone the owner corresponds with, and one person's mail and life events over the years."""

from urllib.parse import urlencode

from django.conf import settings
from django.http import HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST

from towpath import life_groups, people


@require_GET
def everyone(request):
    query = request.GET.get("q", "").strip().lower()
    found = people.everyone(settings.TOWPATH_STORE_DIR, limit=10**6)
    if query:
        found = [p for p in found if query in (p["name"] or "").lower() or query in p["address"]]
    return render(request, "people.html", {"nav": "people", "people": found[:300], "query": request.GET.get("q", ""),
                                           "total": len(found)})


@require_GET
def one(request, address):
    main = people.primary(settings.TOWPATH_STORE_DIR, address)
    if main != address.lower():  # a joined address: its person's page
        return redirect(f"/people/{main}/")
    found = people.person(settings.TOWPATH_STORE_DIR, address)
    found["suggested"] = people.suggestions(settings.TOWPATH_STORE_DIR, address)
    peak = max((y["received"] + y["sent"] for y in found["years"]), default=0)
    for y in found["years"]:
        y["width"] = round((y["received"] + y["sent"]) / peak * 100) if peak else 0
    events = []
    for c in found["events"]:
        cit = c.citations[0] if c.citations else None
        events.append({"statement": c.statement, "when": c.when.expression, "state": c.state,
                       "url": "/ref/?" + urlencode({"r": cit.ref}) if cit and cit.ref else None})
    memories = [g for g in life_groups.groups(settings.TOWPATH_STORE_DIR)
                if g["kind"] == "person" and address.lower() in g["title"].lower() or
                g["kind"] == "person" and found["name"] and found["name"] in g["title"]]
    return render(request, "person.html", {"nav": "people", "p": found, "events": events, "memories": memories,
                                           "saved": request.GET.get("saved")})


@require_POST
def join(request, address):
    """Join another address to this person, or separate one again (the owner's decision)."""
    try:
        if request.POST.get("separate"):
            people.separate(settings.TOWPATH_STORE_DIR, request.POST["separate"])
        else:
            people.join(settings.TOWPATH_STORE_DIR, address, request.POST.get("other", ""))
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))
    return redirect(f"/people/{people.primary(settings.TOWPATH_STORE_DIR, address)}/?saved=1")
