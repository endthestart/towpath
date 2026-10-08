import sqlite3
from pathlib import Path
from urllib.parse import urlencode

from django.conf import settings
from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from towpath.web import index


class LocalPrivacyMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.get_host()  # Enforce ALLOWED_HOSTS even without Django's common middleware.
        response = self.get_response(request)
        response["Cache-Control"] = "no-store"
        response["X-Frame-Options"] = "DENY"
        response["X-Content-Type-Options"] = "nosniff"
        # Not "no-referrer": browsers then send "Origin: null" with every form post, which the CSRF check rejects.
        # "same-origin" still sends nothing, not even the origin, to other sites.
        response["Referrer-Policy"] = "same-origin"
        response["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self'; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        return response


class StoreSchemaMiddleware:
    """A store older than this version answers with an actionable 503, never a raw database error."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        from towpath import stores

        if isinstance(exception, stores.SchemaOutdated) or (
                isinstance(exception, sqlite3.OperationalError)
                and str(exception).startswith(("no such table", "no such column"))):
            response = render(request, "upgrade.html", {"nav": None, "command": stores.UPGRADE_COMMAND,
                                                        "reports": getattr(exception, "reports", [])})
            response.status_code = 503
            return response
        return None


@require_GET
def overview(request):
    return render(request, "overview.html", {"nav": "overview", **index.overview(settings.TOWPATH_STORE_DIR)})


@require_GET
def emails(request):
    try:
        page = int(request.GET.get("page", "1"))
    except ValueError:
        page = 1
    data = index.emails(settings.TOWPATH_STORE_DIR, request.GET.get("q", ""), request.GET.get("view", "all"), page)
    for name, offset in (("previous", -1), ("next", 1)):
        data[name + "_url"] = "?" + urlencode({"q": data["query"], "view": data["view"], "page": data["page"] + offset})
    return render(request, "emails.html", {"nav": "email", **data})


@require_GET
def email(request, item_id):
    item = index.email(settings.TOWPATH_STORE_DIR, item_id)
    if item is None:
        raise Http404
    return render(request, "email.html", {"nav": "email", "item": item})


@require_GET
def stylesheet(request):
    return HttpResponse((Path(__file__).parent / "assets" / "app.css").read_text(), content_type="text/css")


@require_GET
def htmx_script(request):
    """htmx, vendored and pinned (see htmx.LICENSE): parts of a page that track background work fetch their own
    fragment every few seconds, and nothing else on the page changes."""
    return HttpResponse((Path(__file__).parent / "assets" / "htmx.min.js").read_text(), content_type="text/javascript")


@require_GET
def connections_elsewhere(request):
    """Only reached when no reverse proxy sends /connections/ to the connector (for example in development)."""
    return render(request, "connections_elsewhere.html", {"nav": "connections"})
