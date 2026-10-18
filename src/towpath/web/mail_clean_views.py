"""Mail pages for finding and cleaning up (milestone 4): search, space, clean-up, unsubscribe and archives.

Every change made here is the owner asking for it: archive-folder moves and unsubscribes become ordinary mail
changes (dry runs until turned on); archives to a file are requests the connector carries out, removing mail from the
server only after each copy is verified, and only as the owner asked.
"""

from contextlib import closing
from datetime import datetime, timedelta, timezone

from django.conf import settings
from django.http import HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from towpath.mail import actions, archive, rules, search as mail_search, senders, settings as mail_settings
from towpath import connections
from towpath.mail import unsubscribe
from towpath.stores import open_store, store_path
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


# -- clean-up -----------------------------------------------------------------------------------------------------

def _criteria(source) -> dict:
    cats = [c for c in source.getlist("categories") if c in senders.CATEGORIES] if hasattr(source, "getlist") else []
    days = source.get("older_than", "30")
    return {"mailbox": (source.get("mailbox") or "INBOX").strip(), "categories": cats,
            "older_than": int(days) if str(days).isdigit() else 30, "read_only": source.get("read_only") == "1",
            "sender": (source.get("sender") or "").strip().lower()}


def candidates(store_dir, sid: str, c: dict, limit: int = LIMIT) -> list[dict]:
    """Messages on the server matching the clean-up criteria, newest first. Flagged mail is never included."""
    if not store_path(store_dir, "mail").exists():
        return []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=c["older_than"])).isoformat()
    cats = senders.categories(store_dir, sid)
    where, args = ["source_id = ?", "coalesce(date_utc, '') < ?", "flags NOT LIKE '%Flagged%'"], [sid, cutoff]
    if c["mailbox"] and c["mailbox"] != "*":
        where.append("lower(mailbox) = lower(?)")
        args.append(c["mailbox"])
    if c["sender"]:
        where.append("from_addr = ?")
        args.append(c["sender"])
    if c["read_only"]:
        where.append("flags LIKE '%Seen%'")
    with closing(open_store(store_dir, "mail", "web")) as db:
        rows = [dict(r) for r in db.execute(
            f"SELECT item_id, mailbox, uidvalidity, uid, message_id, subject, from_addr, size, date_utc, list_id "
            f"FROM messages WHERE {' AND '.join(where)} ORDER BY date_utc DESC", args)]
    out = []
    for m in rows:
        m["category"] = senders.message_category(m, cats.get(m["from_addr"]))
        if c["categories"] and m["category"] not in c["categories"]:
            continue
        out.append(m)
    if out:
        with closing(open_store(store_dir, "source", "web")) as src:
            present = set()
            ids = [m["item_id"] for m in out]
            for start in range(0, len(ids), 900):
                chunk = ids[start:start + 900]
                present.update(r[0] for r in src.execute(
                    f"SELECT item_id FROM items WHERE absent_since_run IS NULL AND item_id IN "
                    f"({','.join('?' for _ in chunk)})", chunk))
        out = [m for m in out if m["item_id"] in present]
    return out[:limit]


def _summary(found: list[dict]) -> dict:
    by_cat, by_sender = {}, {}
    for m in found:
        by_cat.setdefault(m["category"], [0, 0])
        by_cat[m["category"]][0] += 1
        by_cat[m["category"]][1] += m["size"] or 0
        by_sender.setdefault(m["from_addr"] or "unknown", [0, 0])
        by_sender[m["from_addr"] or "unknown"][0] += 1
        by_sender[m["from_addr"] or "unknown"][1] += m["size"] or 0
    return {"count": len(found), "bytes": sum(m["size"] or 0 for m in found),
            "categories": sorted(((senders.CATEGORIES.get(k, k), *v) for k, v in by_cat.items()), key=lambda r: -r[1]),
            "senders": sorted(((k, *v) for k, v in by_sender.items()), key=lambda r: -r[1])[:20],
            "examples": found[:10]}


@require_http_methods(["GET", "POST"])
def clean(request, sid):
    account = _managed(sid)
    source = request.POST if request.method == "POST" else request.GET
    c = _criteria(source) if source.get("mailbox") is not None else {
        "mailbox": "INBOX", "categories": list(DEFAULT_CATEGORIES), "older_than": 30, "read_only": False, "sender": ""}
    found = candidates(_store(), sid, c)
    what = request.POST.get("do") if request.method == "POST" else None
    if what in ("archive", "file", "file_remove"):
        if not found:
            return HttpResponseBadRequest("Nothing matches.")
        label = (f"{', '.join(senders.CATEGORIES[x] for x in c['categories']) or 'all categories'} in "
                 f"{c['mailbox']}, older than {c['older_than']} days")
        if what == "archive":
            mail_settings.ask_many(_store(), sid, [actions.make(sid, "archive", actions.message_target(m), {},
                                                                reason=f"Clean-up: {label}") for m in found])
            return redirect(f"/mail/{sid}/clean?saved=archive&n={len(found)}")
        rid = archive.request(_store(), sid, [m["item_id"] for m in found], what == "file_remove", label)
        return redirect(f"/mail/{sid}/archives?saved={rid}")
    return render(request, "mail_clean.html", {
        "nav": "mail", "account": account, "c": c, "summary": _summary(found), "saved": request.GET.get("saved"),
        "saved_n": request.GET.get("n"), "categories": list(senders.CATEGORIES.items()),
        "query": "&".join([f"mailbox={c['mailbox']}", f"older_than={c['older_than']}",
                           *(f"categories={x}" for x in c["categories"]),
                           *(["read_only=1"] if c["read_only"] else []),
                           *([f"sender={c['sender']}"] if c["sender"] else [])])})


# -- unsubscribe --------------------------------------------------------------------------------------------------

def _offers(sid: str) -> list[dict]:
    """Senders whose mail carries List-Unsubscribe, with what Towpath can do for each."""
    rows = [r for r in senders.listing(_store(), sid, limit=10**6) if r["unsubscribe"] and r["category"] != "person"]
    if not rows:
        return []
    host = ((connections.get(_store(), sid, role="web") or {}).get("settings") or {}).get("host") or ""
    with closing(open_store(_store(), "mail", "web")) as db:
        for r in rows:
            latest = db.execute("SELECT list_unsubscribe, list_unsubscribe_post, list_id FROM messages WHERE "
                                "source_id = ? AND from_addr = ? AND list_unsubscribe IS NOT NULL ORDER BY date_utc "
                                "DESC LIMIT 1", (sid, r["address"])).fetchone()
            found = unsubscribe.options(latest[0], latest[1]) if latest else {}
            r["method"] = unsubscribe.best(found, r["address"], latest[2] if latest else None, host)
            r["list_id"] = latest[2] if latest else None
            r["mail_to"] = (r["method"][1][len("mailto:"):].split("?")[0]
                            if r["method"] and r["method"][0] == "mail" else None)
            r["page"] = found.get("page")
    return rows


def _unsubscribed(sid: str) -> set[str]:
    from towpath.mail import executor

    done = {r["body"]["target"].get("sender") for r in executor.receipts(_store(), sid, limit=10**6)
            if r["body"]["type"] == "unsubscribe" and r["outcome"] in ("done", "dry-run")}
    waiting = {w["body"]["target"].get("sender") for w in executor.pending(_store(), role="web")
               if w["body"]["type"] == "unsubscribe"}
    return {a for a in done | waiting if a}


@require_GET
def unsubscribe_page(request, sid):
    account = _managed(sid)
    asked = _unsubscribed(sid)
    rows = _offers(sid)
    for r in rows:
        r["asked"] = r["address"] in asked
    return render(request, "mail_unsubscribe.html", {"nav": "mail", "account": account, "rows": rows[:500],
                                                     "saved": request.GET.get("saved")})


@require_POST
def unsubscribe_ask(request, sid):
    _managed(sid)
    chosen = set(request.POST.getlist("address"))
    offers = {r["address"]: r for r in _offers(sid) if r["address"] in chosen and r["method"]}
    if not offers:
        return HttpResponseBadRequest("Choose senders Towpath can unsubscribe from.")
    for address, r in offers.items():
        method, url = r["method"]
        mail_settings.ask(_store(), sid, actions.make(sid, "unsubscribe", {"sender": address},
                                                      {"method": method, "url": url, "list_id": r.get("list_id")},
                                                      reason="Asked for on the Unsubscribe page"))
        if request.POST.get("archive_existing") == "1":
            found = candidates(_store(), sid, {"mailbox": "INBOX", "categories": [], "older_than": 0,
                                               "read_only": False, "sender": address})
            mail_settings.ask_many(_store(), sid, [actions.make(sid, "archive", actions.message_target(m), {},
                                                                reason=f"Unsubscribed from {address}")
                                                   for m in found])
        if request.POST.get("archive_future") == "1":
            rules.save(_store(), sid, {"name": f"Archive mail from {address}",
                                       "conditions": {"from": address, "mailbox": "INBOX"},
                                       "actions": [{"type": "archive", "params": {}}], "scope": "new"}, mode="auto")
    return redirect(f"/mail/{sid}/unsubscribe?saved={len(offers)}")


# -- archives -----------------------------------------------------------------------------------------------------

@require_GET
def archives_page(request, sid):
    from towpath.mail import executor

    account = _managed(sid)
    receipts = executor.receipts(_store(), sid, limit=10**6)
    rows = []
    for req in archive.requests(_store(), sid, role="web"):
        producer = f"archive:{req['request_id']}"
        removed = sum(1 for r in receipts if r["body"].get("producer") == producer and r["outcome"] == "done")
        dry = sum(1 for r in receipts if r["body"].get("producer") == producer and r["outcome"] == "dry-run")
        p = archive.progress(_store(), req["request_id"])
        rows.append({**req, "total": len(req["item_ids"]), "progress": p, "removed": removed, "dry": dry,
                     "skipped": len(p.get("skipped", [])), "problems": p.get("problems", [])})
    return render(request, "mail_archives.html", {"nav": "mail", "account": account, "rows": rows,
                                                  "saved": request.GET.get("saved")})

