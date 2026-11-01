"""What the search pages share: limits, the adapters, and links to a search or a result."""

from urllib.parse import urlencode

from django.conf import settings
from django.urls import reverse

from towpath import connections, stores
from towpath.unified import sources

PAGE = 20


UNIFIED_STORES = ("source", "queue", "decisions", "files")


def _adapters():
    stores.require_current(settings.TOWPATH_STORE_DIR, UNIFIED_STORES)  # raises SchemaOutdated: a 503 page
    config = getattr(settings, "TOWPATH_CONFIG", None)
    if config is not None:  # accounts added in the UI, read afresh; the web service gets no credential reference
        return sources.build_adapters(connections.merged(config, None, role="web"), connect=False)
    return sources.store_adapters(settings.TOWPATH_STORE_DIR)


def _connection_names() -> dict[str, str]:
    names = {}
    for c in connections.all_connections(settings.TOWPATH_STORE_DIR, role="web"):
        names["files-" + c["source_id"] if c["adapter"] == "recoll" else c["source_id"]] = c["display_name"]
    return names


def _store():
    stores.require_current(settings.TOWPATH_STORE_DIR, UNIFIED_STORES)
    return settings.TOWPATH_STORE_DIR


def _search_url(query: str, chosen: list[str]) -> str:
    return reverse("search") + "?" + urlencode([("q", query), *(("source", s) for s in chosen)])


def _ref_url(ref: str) -> str:
    return reverse("reference") + "?" + urlencode({"r": ref})
