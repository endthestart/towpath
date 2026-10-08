"""The Space page: where the space goes in indexed folders, from what the connector measured after each import.

Read-only, like every page here: it reads the files and connections stores and never touches a file.
"""

from urllib.parse import urlencode

from django.conf import settings
from django.http import Http404
from django.shortcuts import render
from django.views.decorators.http import require_GET

from towpath import connections, folders, space, stores
from towpath.unified import sources

SPACE_STORES = ("files", "decisions", "connections")
SHOWN = 20


def _roots() -> tuple[dict | None, list[dict]]:
    """The folders connection and its roots that may be searched (the same grant as search)."""
    stores.require_current(settings.TOWPATH_STORE_DIR, SPACE_STORES)
    c = connections.get(settings.TOWPATH_STORE_DIR, folders.SID, role="web")
    config = getattr(settings, "TOWPATH_CONFIG", None)
    if c is None or config is None:
        return c, []
    searchable = sources.searchable_roots(connections.merged(config, None, role="web"))
    return c, [r for r in c["settings"].get("roots") or [] if r["alias"] in searchable]


def _db():
    return stores.open_store(settings.TOWPATH_STORE_DIR, "files", "web")


def _share(part: int, whole: int) -> float:
    return round(part / whole, 4) if whole else 0.0


def _folder_url(root: str, path: str) -> str:
    return "/space/folder?" + urlencode({"root": root, "path": path})


def _search_url(text: str) -> str:
    return "/search/?" + urlencode({"q": text, "source": f"files-{folders.SID}"})


def _place(root: dict, path: str, files: int, size: int) -> dict:
    return {"root": root["rel"], "path": path, "files": f"{files:,}", "bytes": size,
            "url": _folder_url(root["alias"], path)}


@require_GET
def overview(request):
    c, roots = _roots()
    db = _db()
    try:
        measured = space.summaries(db, folders.SID, [r["alias"] for r in roots])
        dupes = space.duplicates(db, folders.SID) if roots else None
    finally:
        db.close()
    names = {r["alias"]: r["rel"] for r in roots}
    if dupes:
        for g in dupes["top"]:
            g["places"] = [{"where": f"{names[root]}/{rel}", "url": _folder_url(root, rel.rpartition("/")[0])}
                           for root, rel in g["places"] if root in names]
            g["copies_text"] = f"{g['copies']:,}"
        dupes["top"] = [g for g in dupes["top"] if g["places"]][:SHOWN * 2]
        dupes.update(groups_text=f"{dupes['groups']:,}", files_text=f"{dupes['files']:,}")
    shown = sorted(((r, measured[r["alias"]]) for r in roots if r["alias"] in measured), key=lambda rm: -rm[1]["bytes"])
    total = sum(m["bytes"] for _, m in shown)
    clutter: dict[str, dict] = {}
    types: dict[str, list[int]] = {}
    largest = []
    for r, m in shown:
        for item in m["clutter"]:
            entry = clutter.setdefault(item["kind"], {**item, "places": 0, "files": 0, "bytes": 0, "top": []})
            entry["places"] += item["places"]
            entry["files"] += item["files"]
            entry["bytes"] += item["bytes"]
            entry["top"] += [_place(r, path, n, b) for path, n, b in item["top"]]
        for ext, n, b in m["types"]:
            t = types.setdefault(ext, [0, 0])
            t[0] += n
            t[1] += b
        largest += [{"root": r["rel"], "path": path, "bytes": b,
                     "folder_url": _folder_url(r["alias"], path.rpartition("/")[0])} for path, b in m["largest"]]
    for entry in clutter.values():
        entry["top"] = sorted(entry["top"], key=lambda t: -t["bytes"])[:SHOWN]
        entry.update(share=_share(entry["bytes"], total), files=f"{entry['files']:,}", places=f"{entry['places']:,}")
    type_rows = sorted(([ext, n, b] for ext, (n, b) in types.items()), key=lambda t: -t[2])[:SHOWN]
    updated = max((m["updated_at"] for _, m in shown), default=None)
    return render(request, "space.html", {
        "nav": "space", "connection": c, "busy": bool(c and c["indexing"] in ("requested", "running")),
        "measured": bool(shown), "unmeasured": [r["rel"] for r in roots if r["alias"] not in measured],
        "total_bytes": total, "total_files": f"{sum(m['files'] for _, m in shown):,}",
        "total_folders": f"{sum(m['folders'] for _, m in shown):,}",
        "updated": updated[:16].replace("T", " ") + " UTC" if updated else None,
        "roots": [{"name": r["rel"], "files": f"{m['files']:,}", "bytes": m["bytes"],
                   "share": _share(m["bytes"], total), "url": _folder_url(r["alias"], "")} for r, m in shown],
        "clutter": sorted(clutter.values(), key=lambda e: -e["bytes"]),
        "types": [{"ext": ext or "no extension", "files": f"{n:,}", "bytes": b, "share": _share(b, total),
                   "url": _search_url(f"extension:{ext}") if ext else None} for ext, n, b in type_rows],
        "largest": sorted(largest, key=lambda f: -f["bytes"])[:50],
        "duplicates": dupes if dupes and dupes["top"] else None,
    })


@require_GET
def folder_page(request):
    _, roots = _roots()
    alias, path = request.GET.get("root", ""), request.GET.get("path", "").strip("/")
    root = next((r for r in roots if r["alias"] == alias), None)
    if root is None:
        raise Http404
    db = _db()
    try:
        found = space.folder(db, folders.SID, alias, path)
        listed = (found is not None and 0 < found["here_files"] and found["files"] <= space.FILES_LISTED_UP_TO)
        files = space.files_here(db, folders.SID, alias, path) if listed else []
    finally:
        db.close()
    if found is None:
        raise Http404
    crumbs, walked = [{"name": root["rel"], "url": _folder_url(alias, "")}], ""
    for part in [p for p in path.split("/") if p]:
        walked = f"{walked}/{part}" if walked else part
        crumbs.append({"name": part, "url": _folder_url(alias, walked)})
    biggest = max([s[2] for s in found["subfolders"]] + [found["here_bytes"], 1])
    return render(request, "space_folder.html", {
        "nav": "space", "crumbs": crumbs, "name": crumbs[-1]["name"], "folder": found,
        "subfolders": [{"name": p.rpartition("/")[2], "files": f"{n:,}", "bytes": b, "share": _share(b, biggest),
                        "url": _folder_url(alias, p)} for p, n, b in found["subfolders"]],
        "here_share": _share(found["here_bytes"], biggest), "files_text": f"{found['files']:,}",
        "here_files_text": f"{found['here_files']:,}",
        "more": found["subfolder_count"] - len(found["subfolders"]),
        "search_url": _search_url(path) if path else None,  # catalog paths are relative to the folder chosen
        "files": [{"name": name, "bytes": size, "url": "/ref/?" + urlencode({"r": f"files-{folders.SID}:{occ}"})}
                  for occ, name, size in files],
        "files_unlisted": found["here_files"] > 0 and not files,
    })
