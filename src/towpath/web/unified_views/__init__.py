"""Unified search, reference inspection, selected-content requests and collections.

Everything here runs as the web role in local mode: it reads the source and files stores, writes
owner decisions (collections) to the decisions store, and appends requests to the queue. It loads
no credential, builds no connector and starts no provider process; provider searches and content
fetches are queued for towpath-connect.
"""

from towpath.web.unified_views.collection_pages import (
    collection_accept,
    collection_add,
    collection_create,
    collection_detail,
    collection_list,
    collection_remove,
)
from towpath.web.unified_views.results import reference, reference_request
from towpath.web.unified_views.searching import search, search_ask, search_asked, search_request

__all__ = [
    "collection_accept", "collection_add", "collection_create", "collection_detail", "collection_list",
    "collection_remove", "reference", "reference_request", "search", "search_ask", "search_asked", "search_request",
]
