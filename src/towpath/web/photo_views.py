"""The Photos page: photos found by camera, lens, when and where they were taken, or by a plain-English request."""

from contextlib import closing
from datetime import datetime, timezone
from urllib.parse import urlencode

from django.conf import settings
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST

from towpath import photos
from towpath.stores import open_store, store_path

PAGE = 200


def _store():
    return settings.TOWPATH_STORE_DIR


@require_GET
def page(request):
    f = {k: (request.GET.get(k) or "").strip() for k in photos.FILTERS}
    f["located"] = f["located"] == "1"
    number = int(request.GET.get("page") or 1) if (request.GET.get("page") or "1").isdigit() else 1
    active = any(v for v in f.values())
    rows, total = photos.search(_store(), f, limit=PAGE, offset=(number - 1) * PAGE) if active else ([], 0)
    for r in rows:
        r["ref"] = "/ref/?" + urlencode({"r": f"file:{r['root_alias']}/{r['rel_path']}"})
    query = urlencode({k: ("1" if v is True else v) for k, v in f.items() if v})
    return render(request, "photos.html", {
        "nav": "photos", "f": f, "rows": rows, "total": total, "page": number, "more": number * PAGE < total,
        "facets": photos.facets(_store()), "progress": photos.progress(_store()), "query": query,
        "active": active})


@require_POST
def ask(request):
    text = " ".join(request.POST.get("question", "").split())[:300]
    if not text:
        return HttpResponseBadRequest("Describe the photos you want.")
    with closing(open_store(_store(), "queue", "web")) as q:
        seq = q.execute("INSERT INTO photo_questions (text, author, created_at) VALUES (?, 'owner', ?)",
                        (text, datetime.now(timezone.utc).isoformat(timespec="seconds"))).lastrowid
        q.commit()
    return redirect(f"/photos/ask/{seq}")


@require_GET
def asked(request, seq):
    with closing(open_store(_store(), "queue", "web")) as q:
        row = q.execute("SELECT text FROM photo_questions WHERE seq = ?", (seq,)).fetchone()
    if row is None:
        raise Http404
    answer = None
    if store_path(_store(), "derived").exists():
        import json

        with closing(open_store(_store(), "derived", "web")) as d:
            found = d.execute("SELECT filters FROM photo_answers WHERE seq = ?", (seq,)).fetchone()
        answer = json.loads(found[0]) if found else None
    if answer is not None:
        params = {k: ("1" if v is True else v) for k, v in answer.items() if v and k in photos.FILTERS}
        return redirect("/photos/?" + urlencode(params))
    return render(request, "photos_waiting.html", {"nav": "photos", "text": row[0], "seq": seq})
