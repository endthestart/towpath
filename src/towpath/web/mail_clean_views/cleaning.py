"""Cleaning up mail: choosing what would go, and asking for it."""

from contextlib import closing
from datetime import datetime, timedelta, timezone

from django.http import HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from towpath.mail import actions, archive, senders, settings as mail_settings
from towpath.stores import open_store, store_path
from towpath.web.mail_clean_views.pages import DEFAULT_CATEGORIES, LIMIT, _store
from towpath.web.mail_rules_views import _managed


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
