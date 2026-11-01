"""The search page and asking the connector to search."""

from urllib.parse import urlencode

from django.conf import settings
from django.http import Http404, HttpResponseBadRequest, HttpResponseRedirect
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST

from towpath.unified import collections, federation, requests
from towpath.unified.contracts import ContractError, Filters
from towpath.web import present
from towpath.web.unified_views.common import PAGE, _adapters, _connection_names, _ref_url, _search_url, _store


def _present(response: dict, labels: dict, types: dict) -> list[dict]:
    """Each source's page as plain-language rows, in the source's own order."""
    out = []
    for page in response["sources"]:
        sid = page["source_id"]
        rows = [present.row(r, _ref_url(r["ref"])) for r in response["results"] if r["source_id"] == sid]
        out.append(present.group(page, rows, labels.get(sid, sid), types.get(sid, "")))
    return out


@require_POST
def search_ask(request):
    """A plain-English search: queued for the worker's model, then shown as the query it became."""
    from django.shortcuts import redirect

    from towpath import search_translate

    try:
        seq = search_translate.ask(settings.TOWPATH_STORE_DIR, request.POST.get("question", ""))
    except ValueError:
        return HttpResponseBadRequest("Ask something.")
    return redirect(f"/search/ask/{seq}")


@require_GET
def search_asked(request, seq: int):
    from urllib.parse import urlencode

    from django.shortcuts import redirect

    from towpath import search_translate

    text = search_translate.question(settings.TOWPATH_STORE_DIR, seq)
    if text is None:
        raise Http404
    found = search_translate.answer(settings.TOWPATH_STORE_DIR, seq)
    if found:
        return redirect("/search/?" + urlencode({"q": found["query"], "asked": text}))
    return render(request, "search_waiting.html", {"nav": "search", "text": text})


@require_GET
def search(request):
    query = request.GET.get("q", "").strip()[:512]
    chosen = request.GET.getlist("source")
    cursor = request.GET.get("cursor") or None
    adapters = _adapters()
    statuses = federation.statuses(adapters)
    labels = present.source_labels(statuses, _connection_names())
    types = {s["source_id"]: s["source_type"] for s in statuses}
    deep = present.deep_sources(statuses, chosen)
    context = {"nav": "search", "query": query, "chosen": chosen, "statuses": statuses, "labels": labels,
               "sources": [{**s, "label": labels[s["source_id"]],
                            "checked": not chosen or s["source_id"] in chosen} for s in statuses],
               "deep_labels": [labels[s] for s in deep], "deep_names": present.join([labels[s] for s in deep]),
               "deep_verb": "are" if len(deep) > 1 else "is", "overview": [present.overview(s, labels[s["source_id"]])
                                                                     for s in statuses],
               "error": None, "response": None, "provider": None, "asked": request.GET.get("asked"),
               "waiting": False}
    if query:
        try:
            filters = Filters.parse(query, sources=chosen or None)
            response = federation.search(adapters, filters, PAGE, cursor).to_dict()
        except ContractError as exc:
            context["error"] = str(exc)
        else:
            context.update(response=response, groups=_present(response, labels, types), filters=filters,
                           reasons=present.reasons(response, labels), first_page=cursor is None, page_size=PAGE)
            if cursor is None and filters.words:  # people whose name or address has every word
                from towpath import people

                words = [w.lower() for w in filters.words]
                context["people"] = [p for p in people.everyone(_store(), limit=2000)
                                     if all(w in f"{p['name'] or ''} {p['address']}".lower() for w in words)][:6]
            if response["next_cursor"]:
                context["next_url"] = _search_url(query, chosen) + "&" + urlencode({"cursor": response["next_cursor"]})
            if not filters.metadata_only and deep:
                stored = requests.latest_search(_store(), filters)
                if stored:  # re-check against today's grants, exclusions, scope and catalogs before showing
                    stored["response"] = requests.apply_current_policy(
                        _store(), getattr(settings, "TOWPATH_CONFIG", None), stored["response"])
                    stored["groups"] = _present(stored["response"], labels, types)
                    stored["reasons"] = present.reasons(stored["response"], labels)
                    stored["ago"] = present.ago(stored["ran_at"])
                context.update(provider=stored, waiting=requests.search_waiting(_store(), filters))
                context["live"] = context["waiting"]
                context["deep_url"] = _search_url(query, chosen) + "&part=deep"
    if request.GET.get("part") == "deep":  # the "Inside messages" panel alone, polled while its search runs
        return render(request, "_deep_search.html", context)
    context["set_collections"] = [c for c in collections.list_all(_store()) if c["kind"] == "set"]
    return render(request, "search.html", context)


@require_POST
def search_request(request):
    query = request.POST.get("q", "").strip()[:512]
    chosen = request.POST.getlist("source")
    try:
        filters = Filters.parse(query, sources=chosen or None)
    except ContractError as exc:
        return HttpResponseBadRequest(str(exc))
    requests.request_search(_store(), filters)
    return HttpResponseRedirect(_search_url(query, chosen))
