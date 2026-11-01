"""Recoll's record fields, the sist2 slot, and optional runs against a real Recoll."""

import base64
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.recoll_stub import PAPER_SHA
from towpath import config as config_mod
from towpath.discovery import corpus, evaluate, policy, service
from towpath.discovery.providers import recoll as recoll_provider
from towpath.discovery.providers.base import Unavailable


def test_source_stamp_follows_observed_recoll_fields():
    nested = {"ipath": "mail/backup.mbox:1:0", "pcbytes": "5359", "fbytes": "1298", "fmtime": "01073001600",
              "sig": "53591791074692"}
    assert recoll_provider.source_stamp(nested) == {"size": 5359, "mtime": 1073001600, "ctime": 1791074692,
                                                    "basis": "Recoll pcbytes/fbytes, fmtime, ctime from sig"}
    top = {"ipath": "", "fbytes": "185", "fmtime": "01073001600", "sig": "1851791074692"}
    assert recoll_provider.source_stamp(top)["size"] == 185
    odd = {"ipath": "a:1", "pcbytes": "10", "fmtime": "5", "sig": "something-else"}
    assert recoll_provider.source_stamp(odd)["ctime"] is None
    assert recoll_provider.source_stamp({"ipath": "a:1", "fbytes": "9"}) is None


def test_member_kinds_follow_recoll_records():
    row = {"url": "file:///r/a.zip", "ipath": "mail/backup.mbox:3:1", "mtype": "application/pdf",
           "filename": "scan.pdf", "ancestors": [None, "message/rfc822"]}
    assert [(m.kind, m.name, m.index) for m in recoll_provider.members_from(row)] == [
        ("archive-member", "mail/backup.mbox", None), ("mail-message", None, 3), ("attachment", "scan.pdf", 1)]
    plain = {"url": "file:///r/notes.odt", "ipath": "Pictures/1.png", "mtype": "image/png", "ancestors": []}
    assert [(m.kind, m.name) for m in recoll_provider.members_from(plain)] == [("embedded", "Pictures/1.png")]
    nested = {"url": "file:///r/a.zip", "ipath": "inner.zip:deeper.zip:notes.txt", "mtype": "text/plain",
              "ancestors": []}  # a shortened ID: the enclosing records' types are unknown
    assert [(m.kind, m.name) for m in recoll_provider.members_from(nested)] == [
        ("archive-member", "inner.zip"), ("embedded", "deeper.zip"), ("embedded", "notes.txt")]


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


def _rewrite_paper(zip_path: Path, old: str, new: str) -> None:
    """Replace the paper's text inside the synthetic ZIP > mbox, keeping the message structure."""
    import io
    import zipfile

    with zipfile.ZipFile(zip_path) as z:
        mbox = z.read("mail/backup.mbox")
    before = base64.encodebytes(corpus.docx(old))
    assert before in mbox
    mbox = mbox.replace(before, base64.encodebytes(corpus.docx(new)))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(zipfile.ZipInfo("mail/backup.mbox", corpus.ZIP_DATE), mbox)
    zip_path.write_bytes(buf.getvalue())


@pytest.mark.skipif(_native_recoll() is None, reason="Recoll and its Python binding are not installed")
def test_native_stale_index_then_reindex(tmp_path, monkeypatch):
    """Recoll keeps the old sig until recollindex runs, while its extractor reads the current file."""
    monkeypatch.delenv("PYTHONPATH", raising=False)
    python = _native_recoll()
    corpus.generate(tmp_path)
    conf = tmp_path / "recoll-conf"
    conf.mkdir()
    (conf / "recoll.conf").write_text(f"topdirs = {tmp_path / 'roots'}\n")
    index = ["recollindex", "-c", str(conf)]
    subprocess.run(index, check=True, capture_output=True, timeout=120)
    (tmp_path / "towpath.toml").write_text(corpus.EXAMPLE_CONFIG.split("[[files.providers]]")[0] + (
        f'[[files.providers]]\nid = "recoll"\nadapter = "recoll"\nroots = ["archive"]\n'
        f'confdir = "{conf}"\npython = "{python}"\n'))
    cfg = config_mod.load(tmp_path / "towpath.toml")
    for feature in ("search", "excerpt", "recover"):
        policy.grant(cfg, "archive", feature)

    def paper():
        return next(r for r in service.search(cfg, "zebrafinch")["results"]
                    if r["location"].endswith("message 1 > paper.docx"))

    first = paper()
    assert first["source"]["state"] == "fresh"
    cited = service.excerpt(cfg, first["occurrence_id"])["citation"]
    changed_text = corpus.PAPER_TEXT.replace("songbirds", "herons")
    _rewrite_paper(tmp_path / "roots/archive/2003/old-mail.zip", corpus.PAPER_TEXT, changed_text)

    described = service.describe(cfg, first["occurrence_id"])
    assert (described["state"], described["provider_version"], described["source"]) == ("changed", "same", "changed")
    with pytest.raises(service.Stale):
        service.excerpt(cfg, first["occurrence_id"])
    with pytest.raises(service.Stale):
        service.recover(cfg, first["occurrence_id"])
    assert service.resolve_citation(cfg, cited)["state"] == "stale"

    subprocess.run(index, check=True, capture_output=True, timeout=120)
    second = paper()
    assert second["occurrence_id"] == first["occurrence_id"] and second["version"] != first["version"]
    assert second["source"]["state"] == "fresh"
    fresh = service.excerpt(cfg, second["occurrence_id"])
    assert "herons" in fresh["text"] and "songbirds" not in fresh["text"]
    assert service.resolve_citation(cfg, cited)["state"] == "stale"
    assert service.resolve_citation(cfg, fresh["citation"])["state"] == "current"
    recovered = service.recover(cfg, second["occurrence_id"])
    assert recovered["sha256"] == hashlib.sha256(corpus.docx(changed_text)).hexdigest()


@pytest.mark.skipif(_native_recoll() is None, reason="Recoll and its Python binding are not installed")
def test_native_listing_exhaustion(tmp_path, monkeypatch):
    """Real Recoll lists folders as rows (filtered by the adapter); completeness follows raw rows."""
    monkeypatch.delenv("PYTHONPATH", raising=False)
    python = _native_recoll()
    corpus.generate(tmp_path)
    conf = tmp_path / "recoll-conf"
    conf.mkdir()
    (conf / "recoll.conf").write_text(f"topdirs = {tmp_path / 'roots'}\n")
    subprocess.run(["recollindex", "-c", str(conf)], check=True, capture_output=True, timeout=120)
    (tmp_path / "towpath.toml").write_text(corpus.EXAMPLE_CONFIG.split("[[files.providers]]")[0] + (
        f'[[files.providers]]\nid = "recoll"\nadapter = "recoll"\nroots = ["archive"]\n'
        f'confdir = "{conf}"\npython = "{python}"\n'))
    cfg = config_mod.load(tmp_path / "towpath.toml")
    policy.grant(cfg, "archive", "search")
    prov = service.provider(cfg)
    full = prov.enumerate(prov.roots["archive"], 1000, 60)
    assert full.exhausted is True and full.raw_rows > len(full.hits)  # folder rows were filtered
    exact = prov.enumerate(prov.roots["archive"], full.raw_rows, 60)
    assert exact.exhausted is True
    assert prov.enumerate(prov.roots["archive"], full.raw_rows - 1, 60).exhausted is False
    complete = service.import_catalog(cfg, root="archive", max_items=full.raw_rows)[0]
    assert complete["complete"] and complete["marked_missing"] == 0
    capped = service.import_catalog(cfg, root="archive", max_items=1)[0]
    assert (capped["complete"], capped["absence_established"], capped["marked_missing"]) == (False, False, 0)
