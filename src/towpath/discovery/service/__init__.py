"""File discovery operations: probe, status, search, describe, excerpt, recover, import, citations.

Runs as the connect role (writes ``files.db``; reads grants from the
decisions store). Every provider row is re-checked here: it must resolve inside
a configured root, not be excluded, and come from a root with the needed grant.
Rows that fail are dropped before anything is counted, so a broad provider index
cannot leak results, totals, or snippets from places without a grant.
"""

from towpath.discovery.service.catalog import import_catalog, probe, search, status, stored_record
from towpath.discovery.service.common import (
    Disabled,
    DiscoveryError,
    NotFound,
    Stale,
    Unverifiable,
    error_code,
    files_config,
    provider,
)
from towpath.discovery.service.content import current_state, describe, excerpt, recover, resolve_citation

__all__ = [
    "Disabled", "DiscoveryError", "NotFound", "Stale", "Unverifiable", "current_state", "describe", "error_code",
    "excerpt", "files_config", "import_catalog", "probe", "provider", "recover", "resolve_citation", "search",
    "status", "stored_record",
]
