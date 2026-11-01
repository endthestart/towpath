"""Unified adapters over the sources Towpath already reads.

Two modes, chosen by the caller:

- **local** (any role, including the web UI): reads only the source and files stores, read-only.
  Every page is ``catalog`` depth. No credential is loaded, no provider is contacted, and no
  provider subprocess is started.
- **connect** (``towpath-connect``): may also ask the providers themselves. Gmail answers keyword
  text with ``messages.list q`` through the existing paced client; IMAP with ``SEARCH TEXT`` over a
  read-only session; a files provider with its own content index (for example Recoll).

Metadata-only queries (``extension:nef``) are always answered from the catalogs.
"""

from towpath.unified.sources.building import build_adapters, store_adapters
from towpath.unified.sources.common import _fts_query, _like
from towpath.unified.sources.files import searchable_roots
from towpath.unified.sources.mail import MailAdapter

__all__ = [
    "MailAdapter", "_fts_query", "_like", "build_adapters", "searchable_roots", "store_adapters",
]
