"""Mail pages for senders and categories, and for the owner's rules (milestone 3)."""

import json

from django.conf import settings
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from towpath.mail import actions, rules, senders
from towpath.web.auth import local_next
from towpath.web.mail_views import _account, _config

PAGE = 200
RULE_ACTIONS = [(k, v["label"]) for k, v in actions.TYPES.items()
                if k not in actions.APPEND_TYPES and k not in actions.SENDER_TYPES]


def _store():
    return settings.TOWPATH_STORE_DIR


def _managed(sid: str) -> dict:
    account = _account(_config(), sid)
    if not account["managed"]:
        raise Http404
    return account


# -- senders ----------------------------------------------------------------------------------------------------

@require_GET
def sender_list(request, sid):
    account = _managed(sid)
    category = request.GET.get("category") or None
    if category and category not in senders.CATEGORIES:
        category = None
    order = request.GET.get("order") if request.GET.get("order") in ("messages", "recent", "space", "unread") \
        else "messages"
    page = max(int(request.GET.get("page") or 1), 1) if (request.GET.get("page") or "1").isdigit() else 1
    rows = senders.listing(_store(), sid, category, order, limit=PAGE + 1, offset=(page - 1) * PAGE)
    from towpath.mail import privacy

    kept = privacy.kept(_store(), [privacy.sender_target(sid, r["address"]) for r in rows])
    for r in rows:
        r["kept"] = privacy.sender_target(sid, r["address"]) in kept
    return render(request, "mail_senders.html", {
        "nav": "mail", "account": account, "category": category, "order": order, "page": page,
        "rows": rows[:PAGE], "more": len(rows) > PAGE, "totals": senders.totals(_store(), sid),
        "categories": list(senders.CATEGORIES.items()), "saved": request.GET.get("saved"),
        "here": request.get_full_path()})


@require_POST
def sender_privacy(request):
    """Keep a sender's mail (or one message) from cloud models, or allow it again."""
    from towpath.mail import privacy

    sid = request.POST.get("source_id", "")
    _managed(sid)
    target = (privacy.sender_target(sid, request.POST["address"]) if request.POST.get("address") else
              request.POST.get("item_id", ""))
    if not target:
        return HttpResponseBadRequest("Choose a sender or a message.")
    privacy.keep(_config(), target, request.POST.get("keep") == "1")
    return redirect(local_next(request, f"/mail/{sid}/senders?saved=1"))


@require_POST
def sender_category(request):
    sid, address = request.POST.get("source_id", ""), request.POST.get("address", "").lower()
    _managed(sid)
    try:
        senders.mark(_store(), sid, address, request.POST.get("category") or None)
    except ValueError:
        return HttpResponseBadRequest("Choose one of the listed categories.")
    return redirect(local_next(request, f"/mail/{sid}/senders?saved=1"))


@require_POST
def sender_category_many(request):
    """One category for every ticked sender."""
    sid = request.POST.get("source_id", "")
    _managed(sid)
    addresses = [a.lower() for a in request.POST.getlist("address") if a][:PAGE]
    if not addresses:
        return HttpResponseBadRequest("Tick the senders to change.")
    try:
        for address in addresses:
            senders.mark(_store(), sid, address, request.POST.get("category") or None)
    except ValueError:
        return HttpResponseBadRequest("Choose one of the listed categories.")
    return redirect(local_next(request, f"/mail/{sid}/senders?saved=1"))


# -- rules ------------------------------------------------------------------------------------------------------

def _row(rule: dict, runs: dict) -> dict:
    run = runs.get((rule["rule_id"], rule["version"]), {})
    body = rule["body"]
    return {**rule, "name": body["name"], "what": ", ".join(actions.describe(
        {"type": a["type"], "params": a["params"]}) for a in body["actions"]),
            "examined": run.get("examined", 0), "matched": run.get("matched", 0),
            "conditions": _describe_conditions(body)}


def _describe_conditions(body: dict) -> str:
    c = body["conditions"]
    parts = [f"{k} contains “{c[k]}”" if not any(ch in c[k] for ch in "*?[") else f"{k} is like “{c[k]}”"
             for k in rules.FIELDS if c.get(k)]
    if c.get("categories"):
        parts.append("category is " + " or ".join(senders.CATEGORIES[x] for x in c["categories"]))
    if c.get("mailbox"):
        parts.append(f"in {c['mailbox']}")
    if body.get("instructions"):
        parts.append(f"and the AI model agrees it is mail that “{body['instructions']}”")
    return "; ".join(parts) or "any message"


def _runs(sid: str) -> dict:
    from contextlib import closing

    from towpath.stores import open_store, store_path

    if not store_path(_store(), "mailintel").exists():
        return {}
    with closing(open_store(_store(), "mailintel", "web")) as db:
        return {(r["rule_id"], r["version"]): dict(r) for r in db.execute(
            "SELECT * FROM rule_runs WHERE source_id = ?", (sid,))}


@require_GET
def rule_list(request, sid):
    account = _managed(sid)
    runs = _runs(sid)
    in_inbox = {c["category"]: c for c in senders.inbox(_store(), sid)["categories"]}
    templates = []
    for key, t in rules.TEMPLATES.items():
        covers = [in_inbox[c] for c in t["conditions"].get("categories", []) if c in in_inbox]
        templates.append({"key": key, "name": t["name"], "inbox": sum(c["messages"] for c in covers),
                          "unread": sum(c["unread"] for c in covers)})
    templates.sort(key=lambda t: -t["inbox"])
    return render(request, "mail_rules.html", {
        "nav": "mail", "account": account, "rows": [_row(r, runs) for r in rules.all_rules(_store(), sid, "web")],
        "templates": templates, "saved": request.GET.get("saved")})


def _form_body(post) -> dict:
    acts = []
    for i in range(3):
        kind = post.get(f"action_{i}") or ""
        param = (post.get(f"param_{i}") or "").strip()
        if kind:
            params = {"keyword": param} if kind in ("add_keyword", "remove_keyword") else (
                {"mailbox": param} if kind == "move" else {})
            acts.append({"type": kind, "params": params})
    return {"name": post.get("name", ""), "instructions": post.get("instructions", ""),
            "conditions": {**{k: post.get(k, "") for k in rules.FIELDS}, "categories": post.getlist("categories"),
                           "mailbox": post.get("mailbox", "")},
            "actions": acts, "scope": post.get("scope", "new")}


def _form_context(account, body: dict, rule: dict | None, error=None, preview=None) -> dict:
    acts = list(body.get("actions") or []) + [{"type": "", "params": {}}] * 3
    return {"nav": "mail", "account": account, "rule": rule, "body": body, "error": error, "preview": preview,
            "conditions": body.get("conditions") or {}, "categories": list(senders.CATEGORIES.items()),
            "action_rows": [{"i": i, "type": a["type"], "param": next(iter(a["params"].values()), "")}
                            for i, a in enumerate(acts[:3])],
            "action_choices": RULE_ACTIONS}


@require_POST
def rule_describe(request, sid):
    from towpath.mail import rule_drafts

    _managed(sid)
    try:
        seq = rule_drafts.ask(_store(), sid, request.POST.get("description", ""))
    except ValueError:
        return HttpResponseBadRequest("Describe the rule.")
    return redirect(f"/mail/{sid}/rules/drafted/{seq}")


@require_GET
def rule_drafted(request, sid, seq):
    from towpath.mail import rule_drafts

    account = _managed(sid)
    asked = rule_drafts.request(_store(), seq)
    if asked is None or asked["source_id"] != sid:
        raise Http404
    found = rule_drafts.draft(_store(), seq)
    if found is None:
        return render(request, "mail_rule_waiting.html", {"nav": "mail", "account": account, "text": asked["text"]})
    context = _form_context(account, found["body"], None)
    context["drafted_from"] = asked["text"]
    context["draft_status"] = found["status"]
    return render(request, "mail_rule_edit.html", context)


@require_http_methods(["GET", "POST"])
def rule_edit(request, sid, rule_id=None):
    account = _managed(sid)
    rule = rules.get(_store(), rule_id) if rule_id else None
    if rule_id and (rule is None or rule["source_id"] != sid):
        raise Http404
    if request.method == "GET":
        template = rules.TEMPLATES.get(request.GET.get("template", ""))
        body = rule["body"] if rule else (json.loads(json.dumps(template)) if template else
                                          {"name": "", "conditions": {"mailbox": "INBOX"}, "actions": [],
                                           "scope": "new"})
        return render(request, "mail_rule_edit.html", _form_context(account, body, rule))
    body = _form_body(request.POST)
    try:
        if request.POST.get("preview"):
            found = rules.preview(_store(), sid, body)
            return render(request, "mail_rule_edit.html", _form_context(account, body, rule, preview=found))
        saved = rules.save(_store(), sid, body, rule_id=rule_id)
    except (rules.RuleError, actions.ActionError) as exc:
        response = render(request, "mail_rule_edit.html", _form_context(account, body, rule, error=str(exc)))
        response.status_code = 400
        return response
    return redirect(f"/mail/{sid}/rules?saved={saved['rule_id']}")


@require_POST
def rule_change(request):
    rule = rules.get(_store(), request.POST.get("rule_id", ""))
    if rule is None:
        return HttpResponseBadRequest("That rule no longer exists.")
    sid = rule["source_id"]
    _managed(sid)
    op = request.POST.get("op", "")
    ordered = rules.all_rules(_store(), sid, "web")
    try:
        if op in ("enable", "disable"):
            rules.change(_store(), rule["rule_id"], enabled=op == "enable")
        elif op in rules.MODES:
            rules.change(_store(), rule["rule_id"], mode=op)
        elif op == "delete":
            rules.change(_store(), rule["rule_id"], deleted=True)
        elif op in ("up", "down"):
            i = next(n for n, r in enumerate(ordered) if r["rule_id"] == rule["rule_id"])
            j = i - 1 if op == "up" else i + 1
            if 0 <= j < len(ordered):
                other = ordered[j]
                rules.change(_store(), rule["rule_id"], position=other["position"])
                rules.change(_store(), other["rule_id"], position=rule["position"])
        else:
            return HttpResponseBadRequest("Unknown change.")
    except rules.RuleError as exc:
        return HttpResponseBadRequest(str(exc))
    return redirect(f"/mail/{sid}/rules?saved=1")


@require_GET
def rule_history(request, sid, rule_id):
    account = _managed(sid)
    rule = rules.get(_store(), rule_id)
    if rule is None or rule["source_id"] != sid:
        raise Http404
    found = rules.history(_store(), rule_id)
    from towpath.web.mail_views import _subjects

    names = _subjects([m["item_id"] for m in found["matches"]])
    for m in found["matches"]:
        m["subject"] = (names.get(m["item_id"]) or {}).get("subject")
    return render(request, "mail_rule_history.html", {"nav": "mail", "account": account,
                                                      "rule": _row(rule, _runs(sid)), "history": found})
