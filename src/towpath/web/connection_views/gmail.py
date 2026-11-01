"""Connecting Gmail: the instance's Google client and the sign-in round trip."""

import secrets as pysecrets
from pathlib import Path

from django.conf import settings
from django.core import signing
from django.http import HttpResponseRedirect
from django.shortcuts import redirect, render
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_GET, require_POST

from towpath import connections
from towpath.connections import ConnectionProblem
from towpath.web.connection_views.common import _creds, _get, _redirect_uri, _store, _view

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
