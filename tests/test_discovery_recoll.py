"""Recoll adapter against a stub of Recoll's Python binding, plus an optional native run.

The stub reproduces the module surface and row shapes observed from Recoll 1.36.1
on the synthetic corpus (docs/evaluations/file-discovery-native.md): ``connect``,
``Db.query``/``getDoc``, ``Query.execute``/``fetchone``, ``Doc.get``, and
``rclextract.Extractor``. It is a test aid, not Recoll.
"""

import base64
import hashlib
import json
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from towpath import config as config_mod
from towpath.discovery import corpus, evaluate, policy, service
from towpath.discovery.providers import recoll as recoll_provider
from towpath.discovery.providers.base import Unavailable

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
    st = path.stat() if path.exists() or path.is_symlink() else None
    size, mtime = (st.st_size, int(st.st_mtime)) if st else (0, 0)
    doc = {"url": f"file://{path}", "ipath": ipath, "mtype": mtype, "filename": filename, "fbytes": str(size),
           "dbytes": str(len(text.encode())), "fmtime": f"0{mtime}", "dmtime": dmtime, "sig": f"{size}{mtime}",
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


@pytest.fixture
def rw(tmp_path, monkeypatch):
    return RecollWorkspace(tmp_path, monkeypatch)


def test_probe_reports_binding_and_capabilities(rw):
    probe, = service.probe(rw.config)
    assert probe["available"] and probe["binding"] is True and probe["tool"] == "recoll"
    assert probe["capabilities"]["recover"] == "verified"
    assert probe["capabilities"]["hashes"] == "not-provided"


def test_recoll_rows_become_full_lineage_records(rw):
    rw.grant("archive", "search")
    result = service.search(rw.config, "zebrafinch economy")
    paper = [r for r in result["results"] if r["location"].endswith("paper.docx")]
    assert [r["location"] for r in paper] == [
        "archive:2003/old-mail.zip > mail/backup.mbox > message 1 > paper.docx",
        "archive:2003/old-mail.zip > mail/backup.mbox > message 2 > paper.docx"]
    first = paper[0]
    assert [(m["kind"], m["name"], m["index"]) for m in first["locator"]["members"]] == [
        ("archive-member", "mail/backup.mbox", None), ("mail-message", None, 1), ("attachment", "paper.docx", 0)]
    assert first["locator"]["native_id"].endswith("|mail/backup.mbox:1:0")
    dates = {d["meaning"]: d["value"] for d in first["dates"]}
    assert dates["message-date"] == "2003-05-05T10:00:00Z"
    assert first["version"].startswith("recoll-sig=") and first["hashes"] == {} and first["size"] is None
    assert first["extraction"]["status"] == "indexed" and first["extraction"]["truncated"] is None


def test_broad_recoll_index_cannot_leak(rw):
    rw.grant("archive", "search")
    (rw.conf / "fake-ignore-dir").write_text("")
    result = service.search(rw.config, "zebrafinch")
    assert result["results"]
    blob = json.dumps(result)
    for leaked in ("example.com", "outside", "diary", "escape-link", "shared:", "Private"):
        assert leaked not in blob
    report, = service.import_catalog(rw.config, root="archive")
    assert report["refused_references"] >= 3 and "inode/directory" not in json.dumps(report)


def test_excerpt_recover_and_staleness_through_recoll(rw):
    rw.grant("archive", "search", "excerpt", "recover")
    paper = service.search(rw.config, "zebrafinch economy")["results"][0]
    ex = service.excerpt(rw.config, paper["occurrence_id"], max_bytes=200)
    assert ex["text"] == corpus.PAPER_TEXT[:200] and "<p>" not in ex["text"]
    assert ex["source_extraction_truncated"] is None
    rec = service.recover(rw.config, paper["occurrence_id"])
    assert rec["sha256"] == PAPER_SHA and rec["matches_provider_hash"] is None
    assert Path(rec["recovered_to"]).name == "paper.docx"
    evil = [r for r in service.search(rw.config, "traversal")["results"]][0]
    assert Path(service.recover(rw.config, evil["occurrence_id"])["recovered_to"]).name == "evil.txt"

    cid = ex["citation"]["citation_id"]
    for row in rw.index:
        if row.get("ipath") == "mail/backup.mbox:1:0":
            row["sig"] = "999"
    rw.save()
    assert service.resolve_citation(rw.config, cid)["state"] == "stale"
    with pytest.raises(service.Stale):
        service.excerpt(rw.config, paper["occurrence_id"])
    rw.index = [r for r in rw.index if r.get("ipath") != "mail/backup.mbox:1:0"]
    rw.save()
    assert service.describe(rw.config, paper["occurrence_id"])["state"] == "unavailable"


def test_top_level_recovery_copies_inside_the_root_only(rw):
    rw.grant("archive", "search", "recover")
    note = [r for r in service.search(rw.config, "admin mode")["results"]][0]
    rec = service.recover(rw.config, note["occurrence_id"])
    assert Path(rec["recovered_to"]).read_text() == corpus.INJECTION_TEXT


def test_missing_binding_timeout_and_output_cap_are_unavailable(rw, monkeypatch, tmp_path):
    rw.grant("archive", "search")
    broken = tmp_path / "broken" / "recoll"
    broken.mkdir(parents=True)
    (broken / "__init__.py").write_text("raise ImportError('no binding')\n")
    monkeypatch.setenv("PYTHONPATH", str(broken.parent))
    with pytest.raises(Unavailable, match="binding is not importable"):
        service.search(rw.config, "zebrafinch")
    assert service.probe(rw.config)[0]["available"] is False

    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "stub"))
    (rw.conf / "fake-sleep").write_text("5")
    rw.reload("[files.limits]\ntimeout_seconds = 1")
    with pytest.raises(Unavailable, match="timeout"):
        service.search(rw.config, "zebrafinch")
    (rw.conf / "fake-sleep").unlink()
    rw.path.write_text(rw.path.read_text().replace("timeout_seconds = 1", "max_output_bytes = 200"))
    rw.config = config_mod.load(rw.path)
    with pytest.raises(Unavailable, match="output-limit"):
        service.search(rw.config, "zebrafinch")
    runs = service.status(rw.config)
    assert runs["configured"]


def test_member_kinds_follow_recoll_records():
    row = {"url": "file:///r/a.zip", "ipath": "mail/backup.mbox:3:1", "mtype": "application/pdf",
           "filename": "scan.pdf", "ancestors": [None, "message/rfc822"]}
    assert [(m.kind, m.name, m.index) for m in recoll_provider.members_from(row)] == [
        ("archive-member", "mail/backup.mbox", None), ("mail-message", None, 3), ("attachment", "scan.pdf", 1)]
    plain = {"url": "file:///r/notes.odt", "ipath": "Pictures/1.png", "mtype": "image/png", "ancestors": []}
    assert [(m.kind, m.name) for m in recoll_provider.members_from(plain)] == [("embedded", "Pictures/1.png")]


def test_sist2_slot_probes_and_refuses_the_rest(tmp_path):
    corpus.generate(tmp_path)
    text = corpus.EXAMPLE_CONFIG.split("[[files.providers]]")[0] + (
        '[[files.providers]]\nid = "sist2"\nadapter = "sist2"\nroots = ["archive"]\n'
        f'command = ["{sys.executable}", "-c", "print(\'4.2.3\')"]\n')
    (tmp_path / "towpath.toml").write_text(text)
    cfg = config_mod.load(tmp_path / "towpath.toml")
    policy.grant(cfg, "archive", "search")
    probe, = service.probe(cfg)
    assert probe["version"] == "4.2.3" and probe["capabilities"]["search"] == "not-implemented"
    with pytest.raises(Unavailable, match="capability slot"):
        service.search(cfg, "zebrafinch")
    report, = service.import_catalog(cfg)
    assert report["termination"] == "failed" and "capability slot" in report["reason"]
    assert report["absence_established"] is False


def _native_recoll() -> str | None:
    if shutil.which("recollindex") is None:
        return None
    return evaluate.find_recoll_python(timeout=10)


@pytest.mark.skipif(_native_recoll() is None, reason="Recoll and its Python binding are not installed")
def test_native_recoll_through_the_adapter(tmp_path, monkeypatch):
    monkeypatch.delenv("PYTHONPATH", raising=False)
    python = _native_recoll()
    corpus.generate(tmp_path)
    conf = tmp_path / "recoll-conf"
    conf.mkdir()
    (conf / "recoll.conf").write_text(f"topdirs = {tmp_path / 'roots'}\n")
    subprocess.run(["recollindex", "-c", str(conf)], check=True, capture_output=True, timeout=120)
    text = corpus.EXAMPLE_CONFIG.split("[[files.providers]]")[0] + (
        f'[[files.providers]]\nid = "recoll"\nadapter = "recoll"\nroots = ["archive", "shared"]\n'
        f'confdir = "{conf}"\npython = "{python}"\n')
    (tmp_path / "towpath.toml").write_text(text)
    cfg = config_mod.load(tmp_path / "towpath.toml")
    for feature in ("search", "excerpt", "recover"):
        policy.grant(cfg, "archive", feature)
    results = service.search(cfg, "zebrafinch economy")["results"]
    locations = [r["location"] for r in results]
    assert "archive:2003/old-mail.zip > mail/backup.mbox > message 1 > paper.docx" in locations
    assert not any(loc.startswith("shared:") for loc in locations)
    paper = results[locations.index("archive:2003/old-mail.zip > mail/backup.mbox > message 1 > paper.docx")]
    assert {d["meaning"]: d["value"] for d in paper["dates"]}["message-date"] == "2003-05-05T10:00:00Z"
    assert corpus.PAPER_TEXT[:40] in service.excerpt(cfg, paper["occurrence_id"])["text"]
    assert service.recover(cfg, paper["occurrence_id"])["sha256"] == PAPER_SHA
    assert service.search(cfg, "Private diary")["results"] == []
    assert service.search(cfg, "outside the root")["results"] == []
    report, = service.import_catalog(cfg, root="archive")
    assert report["complete"] and report["occurrences_seen"] > 5
