"""Photo metadata search: camera, lens, when and where a photo was taken, read from its headers.

The connector reads image headers with ExifTool (``-fast2``, headers only, never writing) for photos in the files
catalog, in bounded batches with a resumable cursor, and stores typed fields in the files store. Nothing is copied or
thumbnailed. The owner's private hidden-photo export (any path with ``Exports/Hidden``) is never read at all.

The Photos page filters by camera, lens, date taken and whether a location is recorded; a plain-English question
("photos from my Fuji in Italy in 2019") is turned into those filters by a local model (``search.translate``),
which sees the question and the list of cameras and lenses, never a photo.
"""

from towpath.photos.extraction import (
    PRIVATE,
    PRIVATE_GLOBS,
    extract_pending,
    is_private,
    private_in,
    progress,
    purge_private,
    _taken,
)
from towpath.photos.searching import FILTERS, facets, search
from towpath.photos.translating import translate_pending

__all__ = [
    "FILTERS", "PRIVATE", "PRIVATE_GLOBS", "_taken", "extract_pending", "facets", "is_private", "private_in",
    "progress", "purge_private", "search", "translate_pending",
]
