"""Connection setup pages, served only by towpath-connect (``towpath connect serve-setup``).

These are the only pages that receive a password. Each one is passed straight to the connection test and
then to the connector's credentials folder; it is never rendered, logged, put in a redirect or kept in a
session. The web service links here but never handles these forms. See docs/specs/connections-in-the-ui.md.
"""

from towpath.web.connection_views.adding import add, connection_list
from towpath.web.connection_views.detail import (
    detail,
    disconnect,
    folders,
    further_model,
    indexing_settings,
    live,
    pacing,
    password,
    pause,
    remove_write_password,
    schedule,
    start,
    test_model,
    write_password,
)
from towpath.web.connection_views.gmail import google_callback, google_client, google_start

__all__ = [
    "add", "connection_list", "detail", "disconnect", "folders", "further_model", "google_callback", "google_client",
    "google_start", "indexing_settings", "live", "pacing", "password", "pause", "remove_write_password", "schedule",
    "start", "test_model", "write_password",
]
