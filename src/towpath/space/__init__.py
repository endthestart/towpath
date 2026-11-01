"""Where the space goes in indexed folders: folder sizes, space by type, largest files and common clutter.

The connector computes this after each import, from catalog rows alone (no file is opened), and the web service
reads it. Only top-level files count: an archive's or mailbox's members take no space of their own on disk.
Nothing here deletes or moves anything; it shows what could be cleaned up.
"""

from towpath.space.measuring import KEEP_KEY, KEEP_VALUE, MODIFIED_MONTH, VERSION, measure
from towpath.space.reading import FILES_LISTED_UP_TO, duplicates, files_here, folder, summaries
from towpath.space.rebuilding import find_duplicates, keep_target, marks, marks_digest, rebuild, stale

__all__ = [
    "FILES_LISTED_UP_TO", "KEEP_KEY", "KEEP_VALUE", "MODIFIED_MONTH", "VERSION", "duplicates", "files_here",
    "find_duplicates", "folder", "keep_target", "marks", "marks_digest", "measure", "rebuild", "stale", "summaries",
]
