"""Source freshness: does the file on disk still match what the provider indexed?

A provider's *version* says which state of an occurrence its index holds. That
says nothing about the file on disk now: an index can be stale. Recoll, for
example, keeps the old ``sig`` until it is re-run, while its extractor reads the
file as it is now (verified natively, Recoll 1.36.1). So before Towpath returns
an excerpt, a citation, or recovered bytes, it compares the provider's *source
stamp* (the outer file's size, mtime, and ctime as the index recorded them) with
one ``stat`` of the contained outer file.

The check is bounded (one stat per item, never hashing) and only as strong as
the stamp: a rewrite that keeps size, whole-second mtime, and ctime is invisible
to it. Citations additionally compare a hash of the cited text when re-read.
"""

import os

FIELDS = ("size", "mtime", "ctime")


def live_stamp(path) -> dict | None:
    """Size and whole-second mtime and ctime of ``path`` now, or None if it is gone."""
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return None
    return {"size": st.st_size, "mtime": int(st.st_mtime), "ctime": int(st.st_ctime)}


def compare(indexed: dict | None, live: dict | None) -> tuple[str, list[str], str | None]:
    """Return (state, fields compared, reason). States: fresh, changed, missing, unverifiable."""
    if live is None:
        return "missing", [], "the source file is gone"
    checked = [f for f in FIELDS if indexed and indexed.get(f) is not None]
    if not checked:
        return "unverifiable", [], "the provider records no size or time for the source file"
    differ = [f for f in checked if indexed[f] != live[f]]
    if differ:
        return "changed", checked, (f"the source file's {', '.join(differ)} differ from what the provider "
                                    "indexed; the index is stale (re-index, then search again)")
    return "fresh", checked, None


def same(a: dict | None, b: dict | None) -> bool:
    return a is not None and b is not None and all(a.get(f) == b.get(f) for f in FIELDS)
