"""Folders on the NAS, indexed with Recoll from the Connections page (towpath-connect only).

The owner picks folders under the read-only library mount (``/library`` by default). Towpath writes the
Recoll configuration, runs ``recollindex`` in the background at low priority, shows its progress, and then
lists the index page by page into the files store so names, types, sizes and dates are searchable at once.
File contents are searched through Recoll by the request worker. Pause stops ``recollindex``; it resumes
from what is already indexed. Nothing here writes to the folders being indexed: they are mounted read-only,
and Recoll only reads them. See docs/specs/connections-in-the-ui.md.
"""

from towpath.folders.choices import (
    DEFAULTS,
    LIBRARY_ENV,
    SID,
    choose,
    _inside,
    _jobs,
    library,
    options,
    _outcomes,
    _running,
    set_options,
    tree,
    with_files,
)
from towpath.folders.indexing import _carried, _import, index_pending, _progress, _unmeasured
from towpath.folders.recoll import read_status, _stop, write_conf

__all__ = [
    "DEFAULTS", "LIBRARY_ENV", "SID", "_carried", "_import", "_inside", "_jobs", "_outcomes", "_progress", "_running",
    "_stop", "_unmeasured", "choose", "index_pending", "library", "options", "read_status", "set_options", "tree",
    "with_files", "write_conf",
]
