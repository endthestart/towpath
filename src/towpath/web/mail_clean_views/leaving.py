"""Unsubscribing from senders, and the mail archives."""

from contextlib import closing

from django.http import HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST

from towpath import connections
from towpath.mail import actions, archive, rules, senders, settings as mail_settings
from towpath.mail import unsubscribe
from towpath.stores import open_store
from towpath.web.mail_clean_views.cleaning import candidates
from towpath.web.mail_clean_views.pages import _store
from towpath.web.mail_rules_views import _managed


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
