"""The pages for collections."""

from urllib.parse import urlencode

from django.conf import settings
from django.http import Http404, HttpResponseBadRequest, HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from towpath.unified import collections
from towpath.unified.contracts import ContractError, Filters
from towpath.web.unified_views.common import _adapters, _ref_url, _search_url, _store


@require_GET
def collection_list(request):
    return render(request, "collections.html", {"nav": "collections",
                                                "collections": collections.list_all(_store())})


@require_POST
def collection_create(request):
    name = request.POST.get("name", "")
    query = request.POST.get("q", "").strip()
    try:
        if query:
            filters = Filters.parse(query, sources=request.POST.getlist("source") or None)
            created = collections.create(_store(), name, "query", filters)
        else:
            created = collections.create(_store(), name, "set")
    except (ContractError, collections.CollectionError) as exc:
        return HttpResponseBadRequest(str(exc))
    return HttpResponseRedirect(reverse("collection", args=[created["collection_id"]]))


def _collection(cid: str) -> dict:
    try:
        return collections.get(_store(), cid)
    except collections.CollectionError:
        raise Http404 from None


@require_GET
def collection_detail(request, cid):
    collection = _collection(cid)
    evaluation = collections.evaluate(None if collection["kind"] == "photos" else _adapters(), collection, mode="local",
                                      store_dir=_store(), config=getattr(settings, "TOWPATH_CONFIG", None))
    for item in evaluation["items"]:
        item["url"] = _ref_url(item["ref"])
    query = None
    if collection["kind"] == "photos":
        from towpath import thumbs

        shown = thumbs.available(_store(), [i["occurrence_id"] for i in evaluation["items"]])
        for item in evaluation["items"]:
            item["preview"] = item["occurrence_id"] in shown
            item["turn"] = {3: "rot180", 6: "rot90", 8: "rot270"}.get(shown.get(item["occurrence_id"]), "")
        query = "/photos/?" + urlencode({k: ("1" if v is True else v) for k, v in collection["filters"].items()})
    elif collection["filters"]:
        f = Filters.from_dict(collection["filters"])
        query = _search_url(f.to_query(), list(f.sources))
    return render(request, "collection.html", {"nav": "collections", "collection": collection,
                                               "evaluation": evaluation, "query_url": query})


@require_POST
def collection_add(request, cid):
    collection = _collection(cid)
    try:
        collections.add(_store(), collection["collection_id"], request.POST.get("r", ""),
                        request.POST.get("version") or None, request.POST.get("title") or None,
                        request.POST.get("source_type") or None, request.POST.get("note") or None)
    except (ContractError, collections.CollectionError) as exc:
        return HttpResponseBadRequest(str(exc))
    return HttpResponseRedirect(reverse("collection", args=[collection["collection_id"]]))


@require_POST
def collection_remove(request, cid):
    collection = _collection(cid)
    collections.remove(_store(), collection["collection_id"], request.POST.get("r", ""))
    return HttpResponseRedirect(reverse("collection", args=[collection["collection_id"]]))


@require_POST
def collection_accept(request, cid):
    collection = _collection(cid)
    if collection["kind"] not in ("query", "photos"):
        return HttpResponseBadRequest("only query and photo collections have accepted results")
    evaluation = collections.evaluate(None if collection["kind"] == "photos" else _adapters(), collection, mode="local",
                                      store_dir=_store(), config=getattr(settings, "TOWPATH_CONFIG", None))
    collections.accept(_store(), collection["collection_id"], evaluation)
    return HttpResponseRedirect(reverse("collection", args=[collection["collection_id"]]))
