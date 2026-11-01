"""The ``towpath-connect`` role: the only code that reads sources.

It writes the source index and reads the work queue. It never calls a model
and has no way to change a source.
"""

from towpath.connect.fetching import fetch_requests
from towpath.connect.records import Capped, item_id, _latest_labels
from towpath.connect.reporting import coverage, status
from towpath.connect.syncing import sync

__all__ = [
    "Capped", "_latest_labels", "coverage", "fetch_requests", "item_id", "status", "sync",
]
