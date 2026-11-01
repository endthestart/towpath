"""One result's page, and asking for its content."""

from urllib.parse import urlencode

from django.http import Http404, HttpResponseBadRequest, HttpResponseRedirect
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST

from towpath.unified import collections, federation, requests
from towpath.unified.contracts import ContractError, Reference, split_part
from towpath.web import present
from towpath.web.unified_views.common import _adapters, _ref_url, _search_url, _store

STATES = {"current": "Unchanged since it was indexed", "changed": "Changed since it was indexed; index again to update",
          "missing": "No longer in this folder", "unverifiable": "Couldn't be checked against the file"}


def _file_view(result: dict) -> dict:
    """A file as people read it: name, folder, size, dates and type, with links to its folder."""
    from towpath import folders

    loc = result.get("locator") or {}
    folder = (loc.get("path") or "").rpartition("/")[0]
    modified = next((d["value"][:10] for d in result.get("dates", ()) if d.get("meaning") == "file-modified"), None)
    members = loc.get("members") or []
    view = {"folder": f"{loc.get('root')}/{folder}" if folder else loc.get("root"), "modified": modified,
            "size": present.size_text(result.get("size")), "type": result.get("media_type") or None,
            "inside": " > ".join(m.get("name") or m.get("kind", "") for m in members[:-1]) if members else None,
            "space_url": None, "search_url": None}
    if loc.get("provider") == folders.SID and loc.get("root"):
        view["space_url"] = "/space/folder?" + urlencode({"root": loc["root"], "path": folder})
        if folder:
            view["search_url"] = _search_url(folder, [f"files-{folders.SID}"])
    return view


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
        "file": _file_view(result) if result["source_type"] == "files" else None,
        "file_state": STATES.get(described.get("state")),
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
