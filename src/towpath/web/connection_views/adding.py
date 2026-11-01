"""The list of connections and the pages that add one: mail, exports, contacts and calendars, and models."""

from django.conf import settings
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_GET, require_http_methods

from towpath import connections
from towpath.connections import ConnectionProblem
from towpath.web.connection_views.common import _config_ids, _creds, _flat, _store, _typed_password, _view
from towpath.web.connection_views.detail import _folders_page
from towpath.web.connection_views.gmail import _gmail_page


@require_GET
def connection_list(request):
    config = getattr(settings, "TOWPATH_CONFIG", None)
    if config is not None:
        connections.import_configured(config, _creds())
    listed = {c["source_id"] for c in connections.all_connections(_store())}
    configured = [{"source_id": sid, "adapter": s.adapter} for sid, s in (config.sources.items() if config else ())
                  if s.kind == "mail-provider" and sid not in listed]
    views = [_view(c) for c in connections.all_connections(_store())]
    busy = any(v["status"]["busy"] for v in views)
    template = "_connections_live.html" if request.path.endswith("/list-live") else "connections.html"
    return render(request, template, {"nav": "connections", "connections": views, "any_busy": busy, "live": busy,
                                      "configured": configured, "providers": connections.PROVIDERS})


@sensitive_post_parameters("password")
@require_http_methods(["GET", "POST"])
def add(request, provider):
    if provider == "gmail":
        return _gmail_page(request)
    if provider == "folders":
        return _folders_page(request)
    if provider == "model":
        return _model_page(request)
    if provider in ("fastmail_dav", "icloud_dav"):
        return _dav_page(request, provider)
    if provider == "facebook":
        return _facebook_page(request)
    if provider == "imessage":
        return _messages_page(request)
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
            if "@" not in form["username"] and provider in connections.APP_PASSWORDS:
                raise ConnectionProblem(f"Enter your full {preset['label'].split(' ')[0]} address.")
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


def _messages_page(request):
    from towpath import folders as folder_index

    error = None
    if request.method == "POST":
        try:
            sid = connections.add_messages(_store(), folder_index.library() / request.POST.get("path", "").lstrip("/"),
                                           _config_ids())
        except ConnectionProblem as exc:
            error = str(exc)
        else:
            return redirect(f"/connections/{sid}/")
    response = render(request, "connection_messages.html", {
        "nav": "connections", "error": error, "tree": _flat(folder_index.tree(depth=3)),
        "preset": connections.PROVIDERS["imessage"]})
    response.status_code = 400 if error else 200
    return response


def _facebook_page(request):
    from towpath import folders as folder_index

    error = None
    if request.method == "POST":
        try:
            sid = connections.add_facebook(_store(), folder_index.library() / request.POST.get("path", "").lstrip("/"),
                                           _config_ids())
        except ConnectionProblem as exc:
            error = str(exc)
        else:
            return redirect(f"/connections/{sid}/")
    response = render(request, "connection_facebook.html", {
        "nav": "connections", "error": error, "tree": _flat(folder_index.tree(depth=3)),
        "preset": connections.PROVIDERS["facebook"]})
    response.status_code = 400 if error else 200
    return response


def _dav_page(request, provider: str = "fastmail_dav"):
    from towpath import dav_sync
    from towpath.adapters import dav

    form = {"username": request.POST.get("username", "").strip()}
    error = None
    if request.method == "POST":
        password = request.POST.get("password", "").replace(" ", "").strip()
        try:
            urls = None
            if provider == "icloud_dav":
                if "@" not in form["username"] or not password:
                    raise ConnectionProblem("Enter your Apple Account email and an app-specific password.")
                urls = dav_sync.discover(form["username"], password, dav.ICLOUD)
            found = dav_sync.test(form["username"], password, urls)
            sid = dav_sync.add(_store(), _creds(), form["username"], password, found, urls, taken=_config_ids(),
                               provider=provider)
        except ConnectionProblem as exc:
            error = str(exc)
        else:
            return redirect(f"/connections/{sid}/")
        finally:
            password = None  # noqa: F841
    response = render(request, "connection_dav.html", {"nav": "connections", "form": form, "error": error,
                                                        "provider": provider,
                                                        "preset": dav_sync.PROVIDERS[provider]})
    response.status_code = 400 if error else 200
    return response


def _model_page(request):
    form = {"base_url": request.POST.get("base_url", "").strip(), "model": request.POST.get("model", "").strip(),
            "destination": request.POST.get("destination", "self-hosted")}
    error = None
    if request.method == "POST":
        token = request.POST.get("password", "").strip()
        try:
            if not form["base_url"] or not form["model"]:
                raise ConnectionProblem("Enter the model server's address and the model's name.")
            tested = connections.test_model(form, token)
            sid = connections.add_model(_store(), _creds(), form, token, tested, _config_ids())
        except ConnectionProblem as exc:
            error = str(exc)
        else:
            return redirect(f"/connections/{sid}/")
        finally:
            token = None  # noqa: F841 - drop the only reference as soon as it is stored or rejected
    response = render(request, "connection_model.html", {"nav": "connections", "form": form, "error": error,
                                                          "destinations": connections.MODEL_DESTINATIONS})
    response.status_code = 400 if error else 200
    return response
