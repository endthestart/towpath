"""Durable collections: an owner-defined query, or an explicit set of references.

Collections live in the decisions store (written by the web role, every change logged in
``decision_log``) and hold references and versions only, never contents. Re-syncing, re-importing
or deleting a regenerated index leaves them untouched, and references are stable source identities,
so a collection survives a reindex. Evaluating a collection reports, per reference, whether it is
unchanged, changed, added, removed or unavailable, and whether the evaluation itself was partial.

- A ``set`` collection's members are explicit references, each with the version seen when it was added.
- A ``query`` collection stores its filters. Its ``baseline`` is the result set the owner last
  accepted; evaluation compares the current results with it.
- A ``photos`` collection stores a Photos-page search (camera, lens, dates, located, words) and works like a query
  collection over the photo catalog. Photos hidden in Apple Photos or in the private export never appear in it, and
  an accepted photo that is hidden later is only counted, never listed.
"""

from towpath.unified.collections.evaluating import evaluate
from towpath.unified.collections.storing import (
    KINDS,
    CollectionError,
    accept,
    add,
    create,
    get,
    list_all,
    photo_filters,
    remove,
)

__all__ = [
    "CollectionError", "KINDS", "accept", "add", "create", "evaluate", "get", "list_all", "photo_filters", "remove",
]
