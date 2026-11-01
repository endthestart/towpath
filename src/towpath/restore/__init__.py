"""Restoring the owner's decisions from an export package (towpath.export) into this Towpath.

A restore only adds: a decision already made here is kept as it is, so restoring twice, or into a Towpath the owner
has used since, changes nothing they decided. It first shows what it would add (``plan``), and adds it when the owner
asks (``apply``). Only the owner's own decisions come back: reviews of events, recollections, memories kept or
dismissed, the profile, settled pairs, folders kept off Space's clutter list, per-item marks (who may see an item,
which models may read it), dismissed proposals, collections, questions asked, sender categories, conversation marks,
draft notes and rules. Each row is checked as strictly as the page that made it would check it, so a damaged or
hand-edited file can't leave a decision behind that breaks a page.

What could change mail, reach a model or widen access never comes back from a file: permissions for sources and
models, the model chosen for each task, finding events by itself, an account's dry-run switch and the changes it
allows, digests, labels and filing. Rules come back turned off, asking first, for the owner to review. Mail
decisions belong to an account, matched by its provider and name; an account not connected here yet is skipped
and can be restored later, once it is. Marks on a sender or a message are moved to the matching account's own IDs.
A photo collection comes back as its search: which photos the owner accepted is never written to a file, since a
photo may be hidden later. A review of an event found again under another account's ID starts unreviewed.
"""

from towpath.restore.applying import apply, plan
from towpath.restore.common import MAX_TOTAL, VERSION, RestoreError
from towpath.restore.exporting import package

__all__ = [
    "MAX_TOTAL", "RestoreError", "VERSION", "apply", "package", "plan",
]
