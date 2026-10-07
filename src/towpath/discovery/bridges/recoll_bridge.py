"""Bridge to Recoll's Python binding, run as a separate program.

Recoll's binding is compiled for one Python version, which may not be
Towpath's, so Towpath runs this script with the interpreter named in the
provider config and talks JSON over stdin/stdout. It imports nothing from
Towpath and uses only the standard library plus ``recoll`` (GPL, installed by
the owner; nothing from Recoll is copied here).

Interfaces used, as documented by the installed module (Recoll 1.36.1):
``recoll.connect(confdir=...)``, ``Db.query()``, ``Query.execute(q)``,
``Query.fetchone()``, ``Query.scroll(n, mode="absolute")``, ``Db.getDoc(udi)``, ``Doc`` attributes, and
``rclextract.Extractor(doc).textextract(ipath)`` / ``.idoctofile(ipath, mimetype, ofilename=...)``.

Search rows never include document text; ``excerpt`` is the only text read.
"""

import json
import sys
from html.parser import HTMLParser

FIELDS = ("url", "ipath", "mtype", "filename", "fbytes", "dbytes", "fmtime", "dmtime", "sig", "size", "title",
          "rcludi", "pcbytes")


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        self.skip += tag in {"script", "style", "head"}

    def handle_endtag(self, tag):
        self.skip -= tag in {"script", "style", "head"} and self.skip > 0

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def _plain(text: str, mimetype: str) -> str:
    if mimetype != "text/html":
        return text
    parser = _Text()
    parser.feed(text)
    return "".join(parser.parts).strip()


def _row(doc, db) -> dict:
    """The fields Towpath uses, plus the media type of each enclosing ipath prefix (None when Recoll
    holds no record for it, as for an mbox inside a ZIP). The prefix lookup relies on the observed
    ``rcludi`` form ``<path>|<ipath>`` (Recoll 1.36.1)."""
    row = {name: doc.get(name) for name in FIELDS}
    ipath, udi = row.get("ipath") or "", row.get("rcludi") or ""
    ancestors = []
    if ipath and udi.endswith("|" + ipath):
        base, parts = udi[: -len(ipath)], ipath.split(":")
        for n in range(1, len(parts)):
            parent = _doc(db, base + ":".join(parts[:n]))
            ancestors.append(parent.get("mtype") if parent is not None else None)
    row["ancestors"] = ancestors
    return row


def _db(req):
    from recoll import recoll

    return recoll.connect(confdir=req["confdir"])


def _query(db, text: str, max_rows: int, offset: int = 0) -> tuple[list[dict], bool]:
    """Up to ``max_rows`` rows after the first ``offset``, and whether the query is exhausted: one more
    row is fetched (and not returned) to tell a listing that ended from one that reached the cap. Pages
    of one listing stay consistent while the index is not being updated."""
    q = db.query()
    count = q.execute(text)
    if offset:
        if isinstance(count, int) and offset >= count:
            return [], True
        try:
            q.scroll(offset, mode="absolute")
        except IndexError:
            return [], True
    rows = []
    while True:
        doc = q.fetchone()
        if doc is None:
            return rows, True
        if len(rows) >= max_rows:
            return rows, False
        rows.append(_row(doc, db))


def _doc(db, udi: str):
    doc = db.getDoc(udi)
    if doc is None or not doc.get("url"):
        return None
    return doc


def handle(req: dict) -> dict:
    op = req["op"]
    if op == "probe":
        import recoll.rclextract  # noqa: F401
        from recoll import recoll

        return {"binding": True, "members": sorted(n for n in dir(recoll) if not n.startswith("_"))}
    db = _db(req)
    if op == "search":
        rows, exhausted = [], True
        for folder in req["dirs"]:
            remaining = req["max_rows"] - len(rows)
            if remaining <= 0:
                exhausted = False  # folders left unqueried
                break
            found, done = _query(db, f'{req["query"]} dir:"{folder}"', remaining)
            rows += found
            exhausted = exhausted and done
        return {"rows": rows, "exhausted": exhausted}
    if op == "enumerate":
        rows, exhausted = _query(db, f'dir:"{req["dir"]}"', req["max_rows"], req.get("offset", 0))
        return {"rows": rows, "exhausted": exhausted}
    doc = _doc(db, req["udi"])
    if doc is None:
        return {"error": "missing", "detail": "no such document in the index"}
    if op == "describe":
        return {"row": _row(doc, db)}
    from recoll import rclextract

    extractor = rclextract.Extractor(doc)
    if op == "excerpt":
        out = extractor.textextract(doc.ipath)
        text = _plain(out.text or "", out.mimetype)
        start = max(0, min(req["start"], len(text)))
        return {"text": text[start:start + req["max_chars"]], "start": start, "total_chars": len(text),
                "total_bytes": len(text.encode("utf-8"))}
    if op == "recover":
        if not doc.ipath:
            return {"error": "top-level", "detail": "top-level files are copied by Towpath, not extracted"}
        extractor.idoctofile(doc.ipath, doc.mtype, ofilename=req["dest"])
        return {"written": True}
    return {"error": "bad-op", "detail": f"unknown op {op!r}"}


def main() -> int:
    try:
        req = json.loads(sys.stdin.read())
        result = handle(req)
    except ImportError:
        result = {"error": "no-binding", "detail": "Recoll's Python binding is not importable by this interpreter"}
    except Exception as exc:  # report the kind of failure, never a traceback or a path
        result = {"error": "failed", "detail": type(exc).__name__}
    sys.stdout.write(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
