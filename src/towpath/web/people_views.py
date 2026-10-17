"""People pages: everyone the owner corresponds with, and one person's mail and life events over the years."""

from urllib.parse import urlencode

from django.conf import settings
from django.shortcuts import render
from django.views.decorators.http import require_GET

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
    found = people.person(settings.TOWPATH_STORE_DIR, address)
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
    return render(request, "person.html", {"nav": "people", "p": found, "events": events, "memories": memories})
