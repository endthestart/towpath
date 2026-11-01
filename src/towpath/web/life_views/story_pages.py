"""Reading the life story: the timeline, a year, today, memories, and the story itself."""

from django.conf import settings
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST

from datetime import date
from urllib.parse import urlencode

from towpath import life, life_groups, life_today, stores
from towpath.web.auth import local_next
from towpath.web.life_views.common import LIFE_STORES, _TURN, _row


def _age(birthday: str | None, year: str) -> int | None:
    if not birthday or not year.isdigit():
        return None
    return int(year) - int(birthday[:4])


@require_GET
def timeline(request):
    """Years, newest first: the memories in each year, then events not in any memory."""
    stores.require_current(settings.TOWPATH_STORE_DIR, LIFE_STORES)
    store = settings.TOWPATH_STORE_DIR
    me = life_groups.profile(store)
    claims_ = {c.claim_id: c for c in life.claims(store, limit=10**6) if c.state != "rejected"}
    memories = [g for g in life_groups.groups(store) if g["state"] != "dismissed" and g["kind"] != "chapter"]
    in_group = {m for g in memories for m in g["members"]}
    years: dict[str, dict] = {}
    for g in memories:
        year = (g["earliest"] or "Undated")[:4]
        years.setdefault(year, {"groups": [], "events": []})["groups"].append(
            {**g, "events": [_row(claims_[m]) for m in g["members"] if m in claims_]})
    show_minor = request.GET.get("minor") == "1"
    for c in claims_.values():
        if c.claim_id in in_group or (c.significance == "routine" and not show_minor):
            continue
        years.setdefault(_row(c)["year"], {"groups": [], "events": []})["events"].append(_row(c))
    context = life_today.year_context(store)
    for y in context:
        years.setdefault(y, {"groups": [], "events": []})
    ordered = [{"year": y, "age": _age(me.get("birthday"), y), "context": context.get(y, {}), **years[y]}
               for y in sorted(years, reverse=True) if y != "Undated" or years[y]["events"] or years[y]["groups"]]
    return render(request, "life_timeline.html", {"nav": "life", "years": ordered, "profile": me,
                                                  "show_minor": show_minor})


@require_GET
def year(request, year):
    store = settings.TOWPATH_STORE_DIR
    if not (year.isdigit() and len(year) == 4):
        raise Http404
    found = life_today.one_year(store, year)
    for m in found["months"]:
        m["events"] = [_row(c) for c in m["events"]]
    found["year_events"] = [_row(c) for c in found["year_events"]]
    from towpath import thumbs

    shown = thumbs.available(store, [o for ids in found["samples"].values() for o in ids])
    found["days"] = [(d, n, [(o, _TURN.get(shown[o], "")) for o in found["samples"].get(d, []) if o in shown])
                     for d, n in found["days"]]
    peak = max((m["photos"] for m in found["months"]), default=0)
    mail_peak = max((m["received"] + m["sent"] for m in found["months"]), default=0)
    return render(request, "life_year.html", {
        "nav": "life", "y": found, "age": _age(life_groups.profile(store).get("birthday"), year),
        "previous": str(int(year) - 1), "next": str(int(year) + 1) if int(year) < date.today().year else None,
        "peak": peak or 1, "mail_peak": mail_peak or 1})


@require_GET
def today(request):
    from datetime import date as date_

    try:
        day = date_.fromisoformat(request.GET["day"]) if request.GET.get("day") else None
    except ValueError:
        day = None
    found = life_today.on_this_day(settings.TOWPATH_STORE_DIR, day)
    for y in found["years"]:
        y["events"] = [_row(c) for c in y["events"]]
        for p in y["photos"]:
            p["ref"] = "/ref/?" + urlencode({"r": f"{p['provider_id']}:{p['occurrence_id']}"})
    from towpath import thumbs

    shown = thumbs.available(settings.TOWPATH_STORE_DIR, [p["occurrence_id"] for y in found["years"]
                                                          for p in y["photos"][:8]])
    for y in found["years"]:
        for p in y["photos"]:
            p["preview"] = p["occurrence_id"] in shown
            p["turn"] = {3: "rot180", 6: "rot90", 8: "rot270"}.get(shown.get(p["occurrence_id"]), "")
    return render(request, "life_today.html", {"nav": "life", **found})


@require_GET
def memories(request):
    store = settings.TOWPATH_STORE_DIR
    show = request.GET.get("show", "proposed")
    found = [g for g in life_groups.groups(store) if show == "all" or g["state"] == show]
    counts = {s: sum(g["state"] == s for g in life_groups.groups(store)) for s in ("proposed", "kept", "dismissed")}
    return render(request, "life_memories.html", {"nav": "life", "groups": found, "show": show, "counts": counts,
                                                  "profile": life_groups.profile(store)})


@require_GET
def memory(request, group_id):
    store = settings.TOWPATH_STORE_DIR
    g = life_groups.get(store, group_id)
    if g is None:
        raise Http404
    claims_ = {c.claim_id: c for c in life.claims(store, limit=10**6)}
    query = request.GET.get("q", "").strip().lower()
    candidates = [_row(c) for c in claims_.values() if query and query in c.statement.lower()
                  and c.claim_id not in g["members"]][:30]
    return render(request, "life_memory.html", {
        "nav": "life", "g": g, "events": [_row(claims_[m]) for m in g["members"] if m in claims_],
        "new_events": [_row(claims_[m]) for m in g.get("new_members", []) if m in claims_],
        "kinds": list(life_groups.KINDS.items()), "query": request.GET.get("q", ""), "candidates": candidates})


@require_POST
def memory_decide(request):
    store = settings.TOWPATH_STORE_DIR
    g = life_groups.get(store, request.POST.get("group_id", ""))
    if g is None:
        return HttpResponseBadRequest("That memory no longer exists.")
    action = request.POST.get("action", "")
    members = list(g["members"])
    if action == "remove":
        members = [m for m in members if m != request.POST.get("claim_id")]
    elif action == "add":
        members.append(request.POST.get("claim_id", ""))
    known = {c.claim_id for c in life.claims(store, limit=10**6)}
    members = [m for m in members if m in known]
    if action == "undo":
        life_groups.undecide(store, g["group_id"])
    elif action in ("keep", "remove", "add", "rename"):
        life_groups.decide(store, g, "kept", title=request.POST.get("title") or g["title"],
                           kind=request.POST.get("kind") if request.POST.get("kind") in life_groups.KINDS else None,
                           members=members)
    elif action == "dismiss":
        life_groups.decide(store, g, "dismissed")
    else:
        return HttpResponseBadRequest("Unknown action.")
    return redirect(local_next(request, f"/life/memories/{g['group_id']}/"))


@require_GET
def story(request):
    from django.http import HttpResponse

    from towpath import life_story

    family = request.GET.get("edition") == "family"
    found = life_story.story(settings.TOWPATH_STORE_DIR, family=family)
    from towpath import life_evidence

    copies = life_evidence.kept(settings.TOWPATH_STORE_DIR)
    for n in found["notes"]:
        n["kept"] = n["claim_id"] in copies
    from towpath import thumbs

    lines = [line for _, entries in found["chapters"] for e in entries for line in e["lines"]]
    shown = thumbs.available(settings.TOWPATH_STORE_DIR, [line["photo"] for line in lines if line["photo"]])
    for line in lines:
        line["preview"] = line["photo"] in shown
        line["turn"] = _TURN.get(shown.get(line["photo"]), "")
    if request.GET.get("format") == "md":
        response = HttpResponse(life_story.markdown(found), content_type="text/markdown; charset=utf-8")
        name = "family-edition.md" if family else "my-story.md"
        response["Content-Disposition"] = f'attachment; filename="{name}"'
        return response
    return render(request, "life_story.html", {"nav": "life", **found, "saved": request.GET.get("saved")})
