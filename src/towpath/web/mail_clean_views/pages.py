"""An account's search, space, attachments, purchases and sign-in pages."""

from django.conf import settings
from django.http import HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST

from towpath.mail import archive, search as mail_search, senders
from towpath.web.mail_rules_views import _managed

DEFAULT_CATEGORIES = ("newsletter", "marketing", "notification", "social")
LIMIT = archive.MAX_ITEMS


def _store():
    return settings.TOWPATH_STORE_DIR


@require_GET
def search_page(request, sid):
    account = _managed(sid)
    query = request.GET.get("q", "").strip()
    try:
        rows = mail_search.search(_store(), sid, query, limit=101, before=request.GET.get("before")) if query else []
        error = None
    except mail_search.QueryError as exc:
        rows, error = [], str(exc)
    more = len(rows) > 100
    rows = rows[:100]
    return render(request, "mail_search.html", {"nav": "mail", "account": account, "query": query, "rows": rows,
                                                "error": error, "asked": request.GET.get("asked", "")[:300],
                                                "next_before": rows[-1]["date_utc"] if more and rows else None})


@require_POST
def search_ask(request, sid):
    from towpath.mail import search_ask as asking

    _managed(sid)
    try:
        seq = asking.ask(_store(), sid, request.POST.get("question", ""))
    except ValueError:
        return HttpResponseBadRequest("Ask something.")
    return redirect(f"/mail/{sid}/search/ask/{seq}")


@require_GET
def search_asked(request, sid, seq):
    from urllib.parse import urlencode

    from django.http import Http404

    from towpath.mail import search_ask as asking

    account = _managed(sid)
    asked = asking.question(_store(), seq)
    if asked is None or asked["source_id"] != sid:
        raise Http404
    found = asking.answer(_store(), seq)
    if found:
        return redirect(f"/mail/{sid}/search?" + urlencode({"q": found["query"], "asked": asked["text"]}))
    return render(request, "mail_search_waiting.html", {"nav": "mail", "account": account, "text": asked["text"]})


@require_GET
def space_page(request, sid):
    account = _managed(sid)
    found = senders.space(_store(), sid)
    top = senders.listing(_store(), sid, order="space", limit=25)
    return render(request, "mail_space.html", {"nav": "mail", "account": account, "space": found, "top": top})


@require_GET
def attachments_page(request, sid):
    from towpath.mail import attachments

    account = _managed(sid)
    f = {k: request.GET.get(k, "").strip() for k in ("kind", "q", "year", "sender", "order")}
    page = max(1, int(request.GET["page"])) if request.GET.get("page", "").isdigit() else 1
    rows = attachments.listing(_store(), sid, f, limit=101, offset=(page - 1) * 100)
    filtered = any(f[k] for k in ("kind", "q", "year", "sender"))
    from towpath.mail import filing

    return render(request, "mail_attachments.html", {
        "filing": {**filing.summary(_store(), sid), "mode": filing.mode(_store(), sid, "web"),
                   "modes": list(filing.MODES.items())} if not filtered and page == 1 else None,
        "saved": request.GET.get("saved"),
        "nav": "mail", "account": account, "f": f, "rows": rows[:100], "more": len(rows) > 100, "page": page,
        "summary": None if filtered or page > 1 else attachments.summary(_store(), sid),
        "kinds": list(attachments.LABELS.items()), "orders": [("newest", "Newest"), ("oldest", "Oldest"),
                                                             ("largest", "Largest")]})


@require_POST
def filing_mode(request, sid):
    from towpath.mail import filing

    _managed(sid)
    try:
        filing.set_mode(_store(), sid, request.POST.get("mode", ""))
    except ValueError:
        return HttpResponseBadRequest("Choose one of the listed options.")
    return redirect(f"/mail/{sid}/attachments?saved=filing")


@require_GET
def purchases_page(request, sid):
    from towpath.mail import purchases

    account = _managed(sid)
    q, year = request.GET.get("q", "").strip(), request.GET.get("year", "").strip()
    return render(request, "mail_purchases.html", {
        "nav": "mail", "account": account, "q": q, "year": year,
        "rows": purchases.listing(_store(), sid, q, year), "summary": purchases.summary(_store(), sid),
        "recurring": [] if q or year else purchases.recurring(_store(), sid)})


@require_GET
def accounts_page(request, sid):
    account = _managed(sid)
    rows = senders.accounts(_store(), sid)
    return render(request, "mail_accounts.html", {"nav": "mail", "account": account, "rows": rows})
