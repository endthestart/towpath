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
        response["Referrer-Policy"] = "no-referrer"
        response["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self'; script-src 'none'; img-src 'self'; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        return response


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
