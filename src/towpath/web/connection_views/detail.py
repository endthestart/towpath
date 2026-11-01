"""One connection's pages: its models and folders, indexing, passwords, schedule and disconnecting."""

from datetime import datetime, timezone

from django.conf import settings
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from towpath import connections
from towpath.connections import ConnectionProblem
from towpath.web.connection_views.common import _creds, _get, _store, _typed_password, _view


@require_POST
def test_model(request, sid):
    """Ask a model connection to answer again (a synthetic prompt), and record how long it took."""
    from towpath import credentials

    c = _get(sid)
    if c["adapter"] != "model":
        raise Http404
    try:
        token = credentials.resolve(f"file:{connections.secret_path(_creds(), sid)}")
        tested = connections.test_model(c["settings"], token or "")
    except (ConnectionProblem, credentials.CredentialError) as exc:
        return render(request, "connection_detail.html", {**_detail_context(sid), "error": str(exc)}, status=400)
    finally:
        token = None  # noqa: F841
    connections._update(_store(), sid, "model-tested", {"seconds": tested["seconds"]},
                        settings={**c["settings"], "tested": tested}, last_error=None)
    return redirect(f"/connections/{sid}/?saved=test")


@require_POST
def further_model(request, sid):
    """Add (test with the stored token) or remove another model on an AI model connection's server."""
    c = _get(sid)
    if c["adapter"] != "model":
        raise Http404
    if request.POST.get("remove"):
        connections.remove_model_name(_store(), sid, request.POST["remove"])
        return redirect(f"/connections/{sid}/?saved=models")
    try:
        connections.add_model_name(_store(), _creds(), sid, request.POST.get("model", ""),
                                   request.POST.get("destination") or None)
    except ConnectionProblem as exc:
        response = render(request, "connection_detail.html", {**_detail_context(sid), "error": str(exc)})
        response.status_code = 400
        return response
    return redirect(f"/connections/{sid}/?saved=models")


@require_http_methods(["GET", "POST"])
def folders(request, sid):
    if _get(sid)["adapter"] == "recoll":
        return _folders_page(request)
    c = _get(sid)
    error = None
    if request.method == "POST":
        try:
            connections.choose_folders(_store(), sid, request.POST.getlist("folder"))
            if request.POST.get("start"):
                connections.start_indexing(_store(), _creds(), sid)
        except ConnectionProblem as exc:
            error = str(exc)
        else:
            return redirect(f"/connections/{sid}/")
    chosen = set(c["settings"].get("mailboxes") or ())
    rows = [{**f, "checked": f["name"] in chosen if chosen else f["include"]} for f in c["folders"] or ()]
    response = render(request, "connection_folders.html", {"nav": "connections", "c": _view(c), "folders": rows,
                                                          "error": error, "first_time": not chosen})
    response.status_code = 400 if error else 200
    return response


def _detail_context(sid: str, indexing: dict | None = None) -> dict:
    raw = _get(sid)
    c = _view(raw)
    context = {"nav": "connections", "c": c, "now": datetime.now(timezone.utc), "live": c["status"]["busy"]}
    current = (raw["settings"] or {}).get("schedule") or "off"
    due = connections.next_check(_store(), raw)
    context["schedule"] = {"current": current, "label": connections.SCHEDULES[current][0],
                           "choices": [(k, connections.SCHEDULES[k][0]) for k in connections.schedules_for(raw)],
                           "next": max(due, datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M UTC") if due else None}
    if c["adapter"] == "recoll":
        from towpath import folders as folder_index

        context["indexing"] = indexing or folder_index.options(raw)
    if c["adapter"] == "gmail" and getattr(settings, "TOWPATH_CONFIG", None) is not None:
        from towpath.quota import METHOD_COSTS

        p = connections.gmail_pacing(settings.TOWPATH_CONFIG)
        per_message = METHOD_COSTS["users.messages.get"]  # reading one message is the costliest common request
        per_minute = p.units_per_minute // per_message
        context["pacing"] = {"verified": p.verified_units_per_minute, "per_minute": f"{per_minute:,}",
                             "per_hour": f"{per_minute * 60:,}", "per_day": f"{p.daily_units // per_message:,}"}
    return context


@require_GET
def live(request, sid):
    """The connection page's progress panel alone, polled while it indexes."""
    return render(request, "_connection_live.html", _detail_context(sid))


@require_GET
def detail(request, sid):
    return render(request, "connection_detail.html", {**_detail_context(sid), "saved": request.GET.get("saved")})


@require_POST
def start(request, sid):
    _get(sid)
    try:
        connections.start_indexing(_store(), _creds(), sid)
    except ConnectionProblem as exc:
        return render(request, "connection_detail.html", {**_detail_context(sid), "error": str(exc)}, status=400)
    return redirect(f"/connections/{sid}/")


@require_POST
def pause(request, sid):
    _get(sid)
    connections.pause_indexing(_store(), sid)
    return redirect(f"/connections/{sid}/")


@sensitive_post_parameters("password")
@require_http_methods(["GET", "POST"])
def password(request, sid):
    c = _get(sid)
    error = None
    if request.method == "POST":
        new = _typed_password(request, c["provider"])
        try:
            found = connections.test_imap({k: v for k, v in c["settings"].items() if k != "mailboxes"}, new)
            connections.replace_password(_store(), _creds(), sid, new, found)
        except ConnectionProblem as exc:
            error = str(exc)
        else:
            return redirect(f"/connections/{sid}/")
        finally:
            new = None  # noqa: F841
    response = render(request, "connection_password.html", {"nav": "connections", "c": _view(c), "error": error,
                                                            "preset": connections.PROVIDERS[c["provider"]]})
    response.status_code = 400 if error else 200
    return response


@sensitive_post_parameters("password")
@require_http_methods(["GET", "POST"])
def write_password(request, sid):
    """A second app password, used only by mail management's action runner to change mail you approved."""
    c = _get(sid)
    if c["adapter"] != "imap":
        raise Http404
    error = None
    if request.method == "POST":
        new = _typed_password(request, c["provider"])
        try:
            tested = connections.test_write_imap({k: v for k, v in c["settings"].items()
                                                  if k not in ("mailboxes", "write")}, new)
            connections.save_write_password(_store(), _creds(), sid, new, tested)
        except ConnectionProblem as exc:
            error = str(exc)
        else:
            return redirect(f"/connections/{sid}/?saved=write")
        finally:
            new = None  # noqa: F841
    response = render(request, "connection_write_password.html", {
        "nav": "connections", "c": _view(c), "error": error, "preset": connections.PROVIDERS[c["provider"]]})
    response.status_code = 400 if error else 200
    return response


@require_POST
def remove_write_password(request, sid):
    if _get(sid)["adapter"] != "imap":
        raise Http404
    connections.remove_write_password(_store(), _creds(), sid)
    return redirect(f"/connections/{sid}/?saved=write-removed")


@require_http_methods(["GET", "POST"])
def disconnect(request, sid):
    c = _get(sid)
    if request.method == "POST":
        connections.disconnect(_store(), _creds(), sid)
        return redirect(f"/connections/{sid}/")
    return render(request, "connection_disconnect.html", {"nav": "connections", "c": _view(c)})


@require_POST
def indexing_settings(request, sid):
    from towpath import folders as folder_index

    if _get(sid)["adapter"] != "recoll":
        raise Http404
    defaults = folder_index.DEFAULTS
    form = {k: v for k, v in defaults.items() if v is not False} if request.POST.get("reset") else request.POST
    try:
        folder_index.set_options(settings.TOWPATH_CONFIG, form)
    except ConnectionProblem as exc:
        typed = {k: request.POST.get(k, "") for k in defaults} | {"sniff": bool(request.POST.get("sniff"))}
        return render(request, "connection_detail.html",
                      {**_detail_context(sid, typed), "indexing_error": str(exc)}, status=400)
    return redirect(f"/connections/{sid}/?saved=indexing")


@require_POST
def schedule(request, sid):
    _get(sid)
    try:
        connections.set_schedule(_store(), sid, request.POST.get("schedule", ""))
    except ConnectionProblem as exc:
        return render(request, "connection_detail.html", {**_detail_context(sid), "schedule_error": str(exc)},
                      status=400)
    return redirect(f"/connections/{sid}/?saved=schedule")


@require_POST
def pacing(request, sid):
    _get(sid)
    raw = request.POST.get("verified", "").replace(",", "").strip()
    try:
        connections.set_verified_quota(settings.TOWPATH_CONFIG, int(raw) if raw else None)
    except ValueError:
        error = "Enter the quota as a whole number, or leave it empty."
    except ConnectionProblem as exc:
        error = str(exc)
    else:
        return redirect(f"/connections/{sid}/")
    return render(request, "connection_detail.html", {**_detail_context(sid), "pacing_error": error}, status=400)


def _folders_page(request):
    from towpath import folders

    error = None
    current = connections.get(_store(), folders.SID)
    chosen = {r["path"] for r in (current["settings"]["roots"] if current else [])}
    if request.method == "POST":
        picks = request.POST.getlist("folder")
        try:
            folders.choose(settings.TOWPATH_CONFIG, picks)
            if request.POST.get("start"):
                connections.start_indexing(_store(), _creds(), folders.SID)
        except ConnectionProblem as exc:
            error, chosen = str(exc), set(picks)
        else:
            return redirect(f"/connections/{folders.SID}/")
    library = folders.library()
    response = render(request, "connection_folders_pick.html", {
        "nav": "connections", "tree": folders.tree(library), "library_ok": library.is_dir(), "chosen": chosen,
        "error": error, "first_time": current is None})
    response.status_code = 400 if error else 200
    return response
