"""Loopback-only local WSGI preview with no credential or mailbox-write path."""

from pathlib import Path
import secrets
from wsgiref.simple_server import WSGIRequestHandler, make_server

import django
from django.conf import settings
from django.core.wsgi import get_wsgi_application


def configure(store_dir: Path):
    if not settings.configured:
        settings.configure(
            SECRET_KEY=secrets.token_hex(32), DEBUG=False, ALLOWED_HOSTS=["127.0.0.1", "localhost"],
            ROOT_URLCONF="towpath.web.urls", TOWPATH_STORE_DIR=store_dir, USE_TZ=True, TIME_ZONE="UTC",
            INSTALLED_APPS=[],
            MIDDLEWARE=["towpath.web.views.LocalPrivacyMiddleware"],
            TEMPLATES=[{"BACKEND": "django.template.backends.django.DjangoTemplates",
                        "DIRS": [str(Path(__file__).parent / "templates")], "APP_DIRS": False}],
        )
        django.setup()


class QuietHandler(WSGIRequestHandler):
    def log_message(self, format, *args):
        # Search queries and message identifiers must not enter request logs.
        pass


def launch(store_dir: Path, port: int):
    configure(store_dir)
    with make_server("127.0.0.1", port, get_wsgi_application(), handler_class=QuietHandler) as server:
        server.serve_forever()
