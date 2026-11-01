"""A stub of Recoll's Python binding and a workspace around it, for the Recoll adapter's tests.

The stub reproduces the module surface and row shapes observed from Recoll 1.36.1
on the synthetic corpus (docs/evaluations/file-discovery-native.md): ``connect``,
``Db.query``/``getDoc``, ``Query.execute``/``fetchone``, ``Doc.get``, and
``rclextract.Extractor``. It is a test aid, not Recoll.
"""

import base64
import hashlib
import json
import sys
import textwrap
from pathlib import Path

from towpath import config as config_mod
from towpath.discovery import corpus, policy

PAPER = corpus.docx(corpus.PAPER_TEXT)
PAPER_SHA = hashlib.sha256(PAPER).hexdigest()

STUB = {
    "recoll/__init__.py": "",
    "recoll/recoll.py": '''
import json, os, re, time

class Doc:
    def __init__(self, data):
        self._d = data
        for k, v in data.items():
            if k not in ("text", "bytes_b64"):
                setattr(self, k, v)
    def get(self, name):
        return self._d.get(name)

class Query:
    def __init__(self, docs, confdir):
        self._docs, self._rows, self._conf = docs, [], confdir
    def execute(self, text):
        sleep = os.path.join(self._conf, "fake-sleep")
        if os.path.exists(sleep):
            time.sleep(float(open(sleep).read()))
        # "fake-ignore-dir" simulates a query that escapes its dir: clause (e.g. through OR precedence).
        ignore = os.path.exists(os.path.join(self._conf, "fake-ignore-dir"))
        dirs = [] if ignore else re.findall(r'dir:"([^"]*)"', text)
        words = [w.lower() for w in re.sub(r'dir:"[^"]*"', " ", text).split()]
        self._rows = [d for d in self._docs
                      if all(d["url"].startswith("file://" + x.rstrip("/") + "/") for x in dirs)
                      and all(w in (d.get("text") or "").lower() for w in words)]
        return len(self._rows)
    def fetchone(self):
        return Doc(self._rows.pop(0)) if self._rows else None
    def scroll(self, value, mode="relative"):
        if value > len(self._rows):
            raise IndexError("position out of range")
        self._rows = self._rows[value:]

class Db:
    def __init__(self, confdir):
        self._conf = confdir
        with open(os.path.join(confdir, "fake-index.json")) as f:
            self._docs = json.load(f)
    def query(self):
        return Query(self._docs, self._conf)
    def getDoc(self, udi, idxidx=0):
        for d in self._docs:
            if d["rcludi"] == udi:
                return Doc(d)
        return Doc({})

def connect(confdir=None, extra_dbs=None, writable=False):
    return Db(confdir)
''',
    # The Xapian binding's term iteration, as the bridge uses it to list records by ID: ``Database(path)``,
    # ``allterms(prefix)`` and ``TermIter.skip_to`` (which returns the term it lands on; iteration continues
    # after it). Each record's ID term is "Q" + rcludi, as observed from Recoll 1.36.1.
    "xapian.py": '''
import json, os

class Error(Exception):
    pass

class DatabaseOpeningError(Error):
    pass

class DatabaseModifiedError(Error):
    pass

class _Item:
    def __init__(self, term):
        self.term = term

class TermIter:
    def __init__(self, terms, modified):
        self._terms, self._i, self._modified = terms, 0, modified
    def __iter__(self):
        return self
    def __next__(self):
        if self._modified():
            raise DatabaseModifiedError("the index changed")
        if self._i >= len(self._terms):
            raise StopIteration
        self._i += 1
        return _Item(self._terms[self._i - 1])
    def skip_to(self, term):
        while self._i < len(self._terms) and self._terms[self._i] < term:
            self._i += 1
        return next(self)

class Database:
    def __init__(self, path):
        self._conf = os.path.dirname(path)
        if os.path.exists(os.path.join(self._conf, "fake-no-xapian")):
            raise DatabaseOpeningError("cannot open")
        with open(os.path.join(self._conf, "fake-index.json")) as f:
            self._terms = sorted(("Q" + d["rcludi"]).encode() for d in json.load(f) if d.get("rcludi"))
    def _modified(self):
        marker = os.path.join(self._conf, "fake-modified-once")
        if os.path.exists(marker):
            os.remove(marker)
            return True
        return False
    def allterms(self, prefix=b""):
        return TermIter([t for t in self._terms if t.startswith(prefix)], self._modified)
''',
    "recoll/rclextract.py": '''
import base64

class _Out:
    def __init__(self, text, mimetype):
        self.text, self.mimetype = text, mimetype

class Extractor:
    def __init__(self, doc):
        self._doc = doc
    def textextract(self, ipath):
        text = self._doc.get("text") or ""
        if (self._doc.get("mtype") or "").endswith("wordprocessingml.document"):
            return _Out("<html><head><title>x</title></head><body><p>" + text + "</p></body></html>", "text/html")
        return _Out(text, "text/plain")
    def idoctofile(self, ipath, mimetype, ofilename=""):
        with open(ofilename, "wb") as f:
            f.write(base64.b64decode(self._doc.get("bytes_b64") or ""))
        return ofilename.encode()
''',
}


def _doc(root: Path, rel: str, ipath: str, mtype: str, text: str, filename: str, dmtime: str,
         data: bytes | None = None, extra: dict | None = None) -> dict:
    path = root / rel
    st = path.stat() if path.exists() else None
    size, mtime, ctime = (st.st_size, int(st.st_mtime), int(st.st_ctime)) if st else (0, 0, 0)
    # As observed from Recoll 1.36.1: sig = outer size + whole-second ctime; pcbytes = outer size;
    # fmtime = outer mtime; fbytes = the member's own size for nested items.
    member_size = len(data) if (ipath and data is not None) else size
    doc = {"url": f"file://{path}", "ipath": ipath, "mtype": mtype, "filename": filename,
           "fbytes": str(member_size), "pcbytes": str(size), "dbytes": str(len(text.encode())),
           "fmtime": f"0{mtime}", "dmtime": dmtime, "sig": f"{size}{ctime}",
           "rcludi": f"{path}|{ipath}", "title": filename, "size": str(len(text.encode())), "text": text}
    if data is not None:
        doc["bytes_b64"] = base64.b64encode(data).decode()
    doc.update(extra or {})
    return doc


def fake_index(roots: Path) -> list[dict]:
    """Rows shaped like Recoll 1.36.1's for the synthetic corpus."""
    a, zip_ = roots / "archive", "2003/old-mail.zip"
    rows = [
        {"url": f"file://{a}", "ipath": "", "mtype": "inode/directory", "rcludi": f"{a}|", "text": ""},
        _doc(a, zip_, "", "application/zip", "", "old-mail.zip", "01073001600"),
        _doc(a, zip_, "mail/backup.mbox:1", "message/rfc822", "Final paper Attached is my final paper.",
             "", "01052128800"),
        _doc(a, zip_, "mail/backup.mbox:1:0", corpus.DOCX_TYPE, corpus.PAPER_TEXT, "paper.docx", "01052128800",
             PAPER),
        _doc(a, zip_, "mail/backup.mbox:2", "message/rfc822", "Fwd: Final paper", "", "01052213400"),
        _doc(a, zip_, "mail/backup.mbox:2:0", corpus.DOCX_TYPE, corpus.PAPER_TEXT, "paper.docx", "01052213400",
             PAPER),
        _doc(a, "notes/injection.md", "", "text/plain", corpus.INJECTION_TEXT, "injection.md", "01073001600"),
        _doc(a, "private/diary.txt", "", "text/plain", "Private diary. zebrafinch sighting", "diary.txt",
             "01073001600"),
        _doc(a, "escape-link", "", "inode/symlink", "zebrafinch outside", "escape-link", "0"),
        _doc(a, "tricky/traversal.zip", "../../evil.txt", "text/plain", "zebrafinch traversal member", "evil.txt",
             "01072958400", b"zebrafinch traversal member\n"),
        _doc(roots / "shared", "copy-of-paper.docx", "", corpus.DOCX_TYPE, corpus.PAPER_TEXT,
             "copy-of-paper.docx", "01073001600"),
        # Hostile rows a compromised or misconfigured index could hold.
        {"url": "http://example.com/zebrafinch", "ipath": "", "mtype": "text/plain", "rcludi": "x|",
         "text": "zebrafinch remote", "sig": "1"},
        {"url": f"file://{roots.parent}/outside/secret.txt", "ipath": "", "mtype": "text/plain",
         "rcludi": "y|", "text": "zebrafinch outside", "sig": "1"},
    ]
    return rows


class RecollWorkspace:
    def __init__(self, tmp_path: Path, monkeypatch):
        self.root = tmp_path
        corpus.generate(tmp_path)
        stub = tmp_path / "stub"
        for rel, code in STUB.items():
            (stub / rel).parent.mkdir(parents=True, exist_ok=True)
            (stub / rel).write_text(textwrap.dedent(code))
        monkeypatch.setenv("PYTHONPATH", str(stub))
        self.conf = tmp_path / "recoll-conf"
        self.conf.mkdir()
        self.index = fake_index(tmp_path / "roots")
        self.save()
        text = corpus.EXAMPLE_CONFIG.split("[[files.providers]]")[0] + textwrap.dedent(f"""
            [[files.providers]]
            id = "recoll"
            adapter = "recoll"
            roots = ["archive", "shared"]
            confdir = "{self.conf}"
            python = "{sys.executable}"
            """)
        self.path = tmp_path / "towpath.toml"
        self.path.write_text(text)
        self.config = config_mod.load(self.path)

    def save(self):
        (self.conf / "fake-index.json").write_text(json.dumps(self.index))

    def reload(self, extra: str = ""):
        self.path.write_text(self.path.read_text().replace('recover_dir = "derived/recovered"',
                                                           f'recover_dir = "derived/recovered"\n{extra}'))
        self.config = config_mod.load(self.path)

    def grant(self, root: str, *features: str):
        for feature in features:
            policy.grant(self.config, root, feature)
