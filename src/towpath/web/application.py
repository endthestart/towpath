"""WSGI UI with no credential or mailbox-write path. Loopback by default; every page requires the one account."""

from pathlib import Path
from urllib.parse import urlsplit
from wsgiref.simple_server import WSGIRequestHandler, make_server

import django
from django.conf import settings
from django.core.wsgi import get_wsgi_application


def address_settings(public_url: str | None) -> dict:
    """Accepted hosts and proxy trust for an optional public URL served by a TLS reverse proxy."""
    hosts, extra = ["127.0.0.1", "localhost"], {}
    if public_url:
        parts = urlsplit(public_url)
        if parts.scheme not in ("http", "https") or not parts.hostname or parts.path not in ("", "/"):
            raise ValueError("public URL must look like https://host[:port]")
        hosts.append(parts.hostname)
        extra = {"CSRF_TRUSTED_ORIGINS": [f"{parts.scheme}://{parts.netloc}"]}
        if parts.scheme == "https":
            extra.update(SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"), CSRF_COOKIE_SECURE=True)
    return {"ALLOWED_HOSTS": hosts, **extra}


def configure(store_dir: Path, config=None, public_url: str | None = None, urlconf: str = "towpath.web.urls",
              **extra):
    """``config`` (optional, already loaded) adds file sources and declared scope; secrets in it are never resolved.

    ``public_url`` is the address a TLS reverse proxy serves (for example ``https://towpath.example.org``):
    its host is accepted, its origin may post forms, and the proxy's ``X-Forwarded-Proto`` is trusted.
    """
    if not settings.configured:
        from towpath.web import auth

        settings.configure(
            SECRET_KEY=auth.secret_key(store_dir), DEBUG=False, **address_settings(public_url),
            ROOT_URLCONF=urlconf, TOWPATH_STORE_DIR=store_dir, TOWPATH_CONFIG=config, USE_TZ=True,
            TIME_ZONE="UTC", INSTALLED_APPS=[],
            # Forms that record decisions or queue requests are POSTs protected by Django's CSRF check.
            MIDDLEWARE=["towpath.web.views.LocalPrivacyMiddleware", "towpath.web.auth.LoginRequiredMiddleware",
                        "towpath.web.views.StoreSchemaMiddleware", "django.middleware.csrf.CsrfViewMiddleware"],
            CSRF_COOKIE_SAMESITE="Strict", CSRF_COOKIE_HTTPONLY=True,
            TEMPLATES=[{"BACKEND": "django.template.backends.django.DjangoTemplates",
                        "DIRS": [str(Path(__file__).parent / "templates")], "APP_DIRS": False}],
            **extra,
        )
        django.setup()


class QuietHandler(WSGIRequestHandler):
    def log_message(self, format, *args):
        # Search queries and message identifiers must not enter request logs.
        pass


def launch(store_dir: Path, port: int, config=None, host: str = "127.0.0.1", public_url: str | None = None,
           urlconf: str = "towpath.web.urls", **extra):
    from towpath.web import auth

    configure(store_dir, config, public_url, urlconf, **extra)
    # The web service owns first-run setup; the connector's setup server never announces a code.
    if urlconf == "towpath.web.urls" and auth.account(store_dir) is None:
        auth.setup_code(store_dir)  # printed now, so the log shows it before anyone opens /setup
    with make_server(host, port, get_wsgi_application(), handler_class=QuietHandler) as server:
        server.serve_forever()
