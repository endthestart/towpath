"""Towpath command line (read-only).

Command groups map to roles: ``connect`` is towpath-connect, ``scan`` and
``proposals`` are towpath-worker, and ``item`` and ``scan dismiss`` are the
web role's decisions. Each command runs in its own process.
"""

import typer

from towpath.cli.config_check import config_app
from towpath.cli.connect import connect_app
from towpath.cli.files import files_app
from towpath.cli.fixtures import fixtures_app
from towpath.cli.model import model_app
from towpath.cli.provider import provider_app
from towpath.cli.scan import item_app, proposals_app, scan_app
from towpath.cli.stores import stores_app
from towpath.unified.cli import app as search_app
from towpath.unified.cli import claims_app, collections_app
from towpath.web.cli import app as web_app

app = typer.Typer(no_args_is_help=True, add_completion=False,
                  help="Towpath: read-only tools for mail, documents, photos, and model endpoints.")
app.add_typer(connect_app, name="connect")
app.add_typer(scan_app, name="scan")
app.add_typer(proposals_app, name="proposals")
app.add_typer(item_app, name="item")
app.add_typer(fixtures_app, name="fixtures")
app.add_typer(model_app, name="model")
app.add_typer(provider_app, name="provider")
app.add_typer(config_app, name="config")
app.add_typer(files_app, name="files")
app.add_typer(stores_app, name="stores")
app.add_typer(web_app, name="web")
app.add_typer(search_app, name="search")
app.add_typer(collections_app, name="collections")
app.add_typer(claims_app, name="claims")
