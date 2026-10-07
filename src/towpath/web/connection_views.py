"""Connection setup pages, served only by towpath-connect (``towpath connect serve-setup``).

These are the only pages that receive a password. Each one is passed straight to the connection test and
then to the connector's credentials folder; it is never rendered, logged, put in a redirect or kept in a
session. The web service links here but never handles these forms. See docs/specs/connections-in-the-ui.md.
"""

import secrets as pysecrets
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings
from django.core import signing
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import redirect, render
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from towpath import connections
from towpath.connections import ConnectionProblem

STATUS = {
    "choose-folders": ("Choose folders to finish setup", "warn"),
    "requested": ("Starting to index…", "busy"),
    "running": ("Indexing…", "busy"),
    "paused": ("Paused", "warn"),
    "disconnected": ("Disconnected", "warn"),
}


def _store():
    return settings.TOWPATH_STORE_DIR


def _creds():
    return settings.TOWPATH_CREDENTIALS_DIR


def _config_ids() -> set[str]:
    config = getattr(settings, "TOWPATH_CONFIG", None)
    return set(config.sources) if config is not None else set()


def status(c: dict) -> dict:
    """Plain words and a tone for a connection's current state."""
    if c["state"] in ("choose-folders", "disconnected"):
        text, tone = STATUS[c["state"]]
    elif c["indexing"] in STATUS:
        text, tone = STATUS[c["indexing"]]
    elif c["last_error"]:
        text, tone = "Needs attention", "warn"
    elif c["progress"]:
        text, tone = "Indexed", "ok"
    else:
        text, tone = "Ready to index", "ok"
    return {"text": text, "tone": tone, "busy": c["indexing"] in ("requested", "running")}


def _typed_password(request, provider: str) -> str:
    """Exactly what was typed, except that Fastmail's grouped app passwords lose their spaces."""
    typed = request.POST.get("password", "")
    return typed.replace(" ", "").strip() if provider == "fastmail" else typed


def _redirect_uri(request) -> str:
    base = getattr(settings, "TOWPATH_PUBLIC_URL", None) or f"{request.scheme}://{request.get_host()}"
    return base.rstrip("/") + "/connections/google/callback"


def _view(c: dict) -> dict:
    progress = c["progress"] or {}
    rate = (progress.get("rate") or {}).get("per_minute")
    when = progress.get("at")
    return {**c, "status": status(c), "address": c["settings"].get("username") or f"{c['display_name']} account",
            "indexed": progress.get("indexed"),
            "indexed_text": f"{progress['indexed']:,}" if progress.get("indexed") is not None else None,
            "rate": round(rate) if rate else None, "updated": when[:16].replace("T", " ") + " UTC" if when else None,
            "folders_chosen": c["settings"].get("mailboxes") or []}


def _get(sid: str) -> dict:
    found = connections.get(_store(), sid)
    if found is None:
        raise Http404
    return found


@require_GET
def connection_list(request):
    config = getattr(settings, "TOWPATH_CONFIG", None)
    if config is not None:
        connections.import_configured(config, _creds())
    listed = {c["source_id"] for c in connections.all_connections(_store())}
    configured = [{"source_id": sid, "adapter": s.adapter} for sid, s in (config.sources.items() if config else ())
                  if s.kind == "mail-provider" and sid not in listed]
    return render(request, "connections.html", {
        "nav": "connections", "connections": [_view(c) for c in connections.all_connections(_store())],
        "configured": configured, "providers": connections.PROVIDERS})


@sensitive_post_parameters("password")
@require_http_methods(["GET", "POST"])
def add(request, provider):
    if provider == "gmail":
        return _gmail_page(request)
    if provider not in connections.PROVIDERS:
        raise Http404
    preset = connections.PROVIDERS[provider]
    form = {"username": request.POST.get("username", "").strip(),
            "host": request.POST.get("host", preset["host"] or "").strip(),
            "port": request.POST.get("port", str(preset["port"])).strip(),
            "security": request.POST.get("security", preset["security"])}
    error = None
    if request.method == "POST":
        password = _typed_password(request, provider)
        settings_ = {"host": preset["host"] or form["host"], "username": form["username"],
                     "security": preset["security"] if preset["host"] else form["security"]}
        try:
            settings_["port"] = preset["port"] if preset["host"] else int(form["port"])
            if "@" not in form["username"] and provider == "fastmail":
                raise ConnectionProblem("Enter your full Fastmail address.")
            folders = connections.test_imap(settings_, password)
            sid = connections.add_imap(_store(), _creds(), provider, settings_, password, folders, _config_ids())
        except ValueError:
            error = "The port must be a number."
        except ConnectionProblem as exc:
            error = str(exc)
        else:
            return redirect(f"/connections/{sid}/folders")
        finally:
            password = None  # noqa: F841 - drop the only reference as soon as it is stored or rejected
    response = render(request, "connection_add.html", {"nav": "connections", "provider": provider, "preset": preset,
                                                      "form": form, "error": error})
    response.status_code = 400 if error else 200
    return response


@require_http_methods(["GET", "POST"])
def folders(request, sid):
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


def _detail_context(sid: str) -> dict:
    c = _view(_get(sid))
    context = {"nav": "connections", "c": c, "now": datetime.now(timezone.utc)}
    if c["adapter"] == "gmail" and getattr(settings, "TOWPATH_CONFIG", None) is not None:
        p = connections.gmail_pacing(settings.TOWPATH_CONFIG)
        verified = p.verified_units_per_minute
        context["pacing"] = {"verified": verified, "verified_text": f"{verified:,}" if verified else None,
                             "units": f"{p.units_per_minute:,}", "daily": f"{p.daily_units:,}"}
    return context


@require_GET
def detail(request, sid):
    return render(request, "connection_detail.html", _detail_context(sid))


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


@require_http_methods(["GET", "POST"])
def disconnect(request, sid):
    c = _get(sid)
    if request.method == "POST":
        connections.disconnect(_store(), _creds(), sid)
        return redirect(f"/connections/{sid}/")
    return render(request, "connection_disconnect.html", {"nav": "connections", "c": _view(c)})


# -- Gmail ----------------------------------------------------------------------------------------------

OAUTH_COOKIE = "towpath_google_oauth"


def _gmail_page(request, error=None, status=200):
    reconnect = request.GET.get("reconnect") or request.POST.get("reconnect")
    if reconnect:
        _get(reconnect)
    client = connections.google_client(_creds())
    return render(request, "connection_gmail.html", {
        "nav": "connections", "client": client, "redirect_uri": _redirect_uri(request), "error": error,
        "reconnect": reconnect, "reconnecting": _view(_get(reconnect)) if reconnect else None}, status=status)


@sensitive_post_parameters("client_secret")
@require_POST
def google_client(request):
    try:
        connections.save_google_client(_creds(), request.POST.get("client_id", ""),
                                       request.POST.get("client_secret", ""), _redirect_uri(request))
    except ConnectionProblem as exc:
        return _gmail_page(request, str(exc), status=400)
    return redirect("/connections/add/gmail" + (f"?reconnect={request.POST['reconnect']}"
                                               if request.POST.get("reconnect") else ""))


@require_POST
def google_start(request):
    from towpath import google_oauth

    reconnect = request.POST.get("reconnect") or None
    if reconnect:
        _get(reconnect)
    try:
        url, state, verifier = google_oauth.consent_url(_creds(), _redirect_uri(request))
    except ConnectionProblem as exc:
        return _gmail_page(request, str(exc), status=400)
    response = HttpResponseRedirect(url)
    response.set_cookie(OAUTH_COOKIE, signing.dumps({"state": state, "verifier": verifier, "reconnect": reconnect},
                                                    salt="towpath.google-oauth"),
                        max_age=600, httponly=True, samesite="Lax", secure=request.is_secure(),
                        path="/connections/google/")
    return response


@require_GET
def google_callback(request):
    from towpath import google_oauth

    try:
        pending = signing.loads(request.COOKIES.get(OAUTH_COOKIE, ""), salt="towpath.google-oauth", max_age=600)
    except signing.BadSignature:
        return _gmail_page(request, "The sign-in took too long or was started elsewhere. Connect again.", status=400)
    if request.GET.get("error"):
        message = ("You cancelled at Google, so nothing was connected." if request.GET["error"] == "access_denied"
                   else "Google didn't complete the sign-in. Connect again.")
        return _gmail_page(request, message, status=400)
    if not pysecrets.compare_digest(request.GET.get("state", ""), pending["state"]):
        return _gmail_page(request, "The sign-in couldn't be verified. Connect again.", status=400)
    config = connections.merged(settings.TOWPATH_CONFIG, _creds())
    reconnect = pending.get("reconnect")
    pending_token = Path(_creds()) / f".pending-{pysecrets.token_hex(6)}.token.json"
    try:
        token = google_oauth.finish(_creds(), _redirect_uri(request), request.GET.get("code", ""), pending["verifier"])
        connections._write_secret(pending_token, token)
        address = google_oauth.address(config, reconnect or "gmail", pending_token)
        sid = connections.save_gmail(_store(), _creds(), token, address, reconnect, set(config.sources))
    except ConnectionProblem as exc:
        return _gmail_page(request, str(exc), status=400)
    finally:
        pending_token.unlink(missing_ok=True)
    response = redirect(f"/connections/{sid}/")
    response.delete_cookie(OAUTH_COOKIE, path="/connections/google/", samesite="Lax")
    return response


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
