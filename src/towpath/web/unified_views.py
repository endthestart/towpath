"""Unified search, reference inspection, selected-content requests and collections.

Everything here runs as the web role in local mode: it reads the source and files stores, writes
owner decisions (collections) to the decisions store, and appends requests to the queue. It loads
no credential, builds no connector and starts no provider process; provider searches and content
fetches are queued for towpath-connect.
"""

from urllib.parse import urlencode

from django.conf import settings
from django.http import Http404, HttpResponseBadRequest, HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from towpath import connections, stores
from towpath.unified import collections, federation, requests, sources
from towpath.web import present
from towpath.unified.contracts import ContractError, Filters, Reference, split_part

PAGE = 20


UNIFIED_STORES = ("source", "queue", "decisions", "files")


def _adapters():
    stores.require_current(settings.TOWPATH_STORE_DIR, UNIFIED_STORES)  # raises SchemaOutdated: a 503 page
    config = getattr(settings, "TOWPATH_CONFIG", None)
    if config is not None:  # accounts added in the UI, read afresh; the web service gets no credential reference
        return sources.build_adapters(connections.merged(config, None, role="web"), connect=False)
    return sources.store_adapters(settings.TOWPATH_STORE_DIR)


def _connection_names() -> dict[str, str]:
    names = {}
    for c in connections.all_connections(settings.TOWPATH_STORE_DIR, role="web"):
        names["files-" + c["source_id"] if c["adapter"] == "recoll" else c["source_id"]] = c["display_name"]
    return names


def _store():
    stores.require_current(settings.TOWPATH_STORE_DIR, UNIFIED_STORES)
    return settings.TOWPATH_STORE_DIR


def _search_url(query: str, chosen: list[str]) -> str:
    return reverse("search") + "?" + urlencode([("q", query), *(("source", s) for s in chosen)])


def _ref_url(ref: str) -> str:
    return reverse("reference") + "?" + urlencode({"r": ref})


def _present(response: dict, labels: dict, types: dict) -> list[dict]:
    """Each source's page as plain-language rows, in the source's own order."""
    out = []
    for page in response["sources"]:
        sid = page["source_id"]
        rows = [present.row(r, _ref_url(r["ref"])) for r in response["results"] if r["source_id"] == sid]
        out.append(present.group(page, rows, labels.get(sid, sid), types.get(sid, "")))
    return out


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
               "error": None, "response": None, "provider": None,
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


@require_GET
def reference(request):
    ref = request.GET.get("r", "")
    try:
        parsed = Reference.parse(ref)
        described = federation.describe(_adapters(), ref)
    except (ContractError, federation.UnknownReference):
        raise Http404 from None
    except Exception as exc:  # noqa: BLE001 - shown as the source's error, like a failed search page
        page = federation.error_page(Reference.parse(ref).source_id, exc)
        return render(request, "reference.html", {"nav": "search", "ref": ref, "error": page.error.to_dict()})
    content = None
    _, part_id = split_part(parsed.native)
    result = described["result"]
    if result["source_type"] in {"gmail", "imap"} and part_id is not None:
        content = requests.part_text(_store(), parsed.source_id, parsed.native)
    in_collections = []
    all_collections = collections.list_all(_store())
    for c in all_collections:
        full = collections.get(_store(), c["collection_id"])
        if any(m["ref"] == ref for m in full["members"] + full["baseline"]):
            in_collections.append(c)
    parts = [dict(p, url=_ref_url(p["ref"])) for p in described.get("parts", [])]
    return render(request, "reference.html", {
        "nav": "search", "ref": ref, "described": described, "result": result, "parts": parts, "content": content,
        "part_id": part_id, "in_collections": in_collections,
        "set_collections": [c for c in all_collections if c["kind"] == "set"],
        "described_json": described})


@require_POST
def reference_request(request):
    ref = request.POST.get("r", "")
    try:
        parsed = Reference.parse(ref)
        requests.request_part(_store(), parsed.source_id, parsed.native)
    except (ContractError, ValueError):
        return HttpResponseBadRequest("selected content is requested for one mail part")
    return HttpResponseRedirect(_ref_url(ref))


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
    evaluation = collections.evaluate(_adapters(), collection, mode="local")
    for item in evaluation["items"]:
        item["url"] = _ref_url(item["ref"])
    query = None
    if collection["filters"]:
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
    if collection["kind"] != "query":
        return HttpResponseBadRequest("only query collections have accepted results")
    evaluation = collections.evaluate(_adapters(), collection, mode="local")
    collections.accept(_store(), collection["collection_id"], evaluation)
    return HttpResponseRedirect(reverse("collection", args=[collection["collection_id"]]))
