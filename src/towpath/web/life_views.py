"""The Life page: the life story's settings now, and the claims proposed for review as they arrive.

The owner chooses which mail accounts the life story may use and whether the text of that mail may be sent to the
AI model connected on the Connections page. Both are decisions in the decisions store; nothing is proposed until
both are given.
"""

from django.conf import settings
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from datetime import date
from urllib.parse import urlencode

from towpath import connections, decisions, life, life_ask, life_groups, life_propose, life_today, stores
from towpath.unified import grants
from towpath.web.auth import local_next

_TURN = {3: "rot180", 6: "rot90", 8: "rot270"}  # EXIF orientation to the preview's turn
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
    rows = connections.all_connections(settings.TOWPATH_STORE_DIR, role="web")
    names = {c["source_id"]: c["display_name"] for c in rows}
    out += [{"source_id": c["source_id"], "name": "Photos in your folders" if c["adapter"] == "recoll" else
             c["display_name"], "allowed": FEATURE in allowed.get(c["source_id"], set())}
            for c in rows if c["adapter"] in ("dav", "recoll")]
    for account in out:
        account["name"] = names.get(account["source_id"], account["name"])
    return out


SHOW = {"review": ("proposed",), "minor": ("proposed",), "kept": ("accepted", "corrected"), "rejected": ("rejected",),
        "all": ("proposed", "accepted", "corrected", "rejected")}


def _shown(claim, show: str) -> bool:
    """Whether a claim belongs under a filter: everyday events the model judged routine wait under Minor."""
    if claim.state not in SHOW.get(show, SHOW["review"]):
        return False
    if show == "minor":
        return claim.significance == "routine"
    return show != "review" or claim.significance != "routine"


def _status() -> dict:
    """The proposal job's state and why it can't run, for the page and its live panel."""
    config = _config()
    value = life_propose.request(settings.TOWPATH_STORE_DIR)
    run = life_propose.progress(settings.TOWPATH_STORE_DIR)
    if run is not None and value is not None and run["run_id"] != life_propose.run_id(value):
        run = None  # that run was for an earlier slice
    reason = life_propose.blocked(config, value, role="web")
    running = reason is None and not (run and run["state"] == "done")
    on = life_propose.auto(settings.TOWPATH_STORE_DIR, role="web")
    return {"request": value, "run": run, "blocked": reason, "running": running or on, "auto": on,
            "auto_progress": life_propose.auto_progress(settings.TOWPATH_STORE_DIR) if on else None}


def _row(claim) -> dict:
    citation = claim.citations[0] if claim.citations else None
    return {"id": claim.claim_id, "statement": claim.statement, "when": claim.when.expression,
            "earliest": claim.when.earliest, "latest": claim.when.latest, "precision": claim.when.precision,
            "modality": {"plan": "planned", "occurred": "happened"}.get(claim.modality, claim.modality),
            "state": claim.state, "uncertainty": claim.uncertainty, "significance": claim.significance,
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
    show = show if show in SHOW else "review"
    rows = [_row(c) for c in proposed if _shown(c, show)][:300]
    status = _status()
    year = date.today().year - 1
    return render(request, "life.html", {
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


@require_GET
def export(request):
    """Everything Towpath learned and the owner decided, as one zip to keep."""
    from django.http import HttpResponse

    from towpath import export as export_mod

    name, data = export_mod.build(settings.TOWPATH_STORE_DIR)
    response = HttpResponse(data, content_type="application/zip")
    response["Content-Disposition"] = f'attachment; filename="{name}"'
    return response


@require_GET
def story(request):
    from django.http import HttpResponse

    from towpath import life_story

    found = life_story.story(settings.TOWPATH_STORE_DIR)
    from towpath import thumbs

    lines = [line for _, entries in found["chapters"] for e in entries for line in e["lines"]]
    shown = thumbs.available(settings.TOWPATH_STORE_DIR, [line["photo"] for line in lines if line["photo"]])
    for line in lines:
        line["preview"] = line["photo"] in shown
        line["turn"] = _TURN.get(shown.get(line["photo"]), "")
    if request.GET.get("format") == "md":
        response = HttpResponse(life_story.markdown(found), content_type="text/markdown; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="my-story.md"'
        return response
    return render(request, "life_story.html", {"nav": "life", **found})


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
