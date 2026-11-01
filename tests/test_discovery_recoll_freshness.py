"""Recoll adapter: telling a current source from a changed, missing or unverifiable one."""

import json
import os
import time
from pathlib import Path

import pytest

from towpath.discovery import context, corpus, service
from towpath.discovery.providers import recoll as recoll_provider
from towpath.discovery.providers.base import Unavailable


def _paper(rw):
    return next(r for r in service.search(rw.config, "zebrafinch economy")["results"]
                if r["location"].endswith("message 1 > paper.docx"))


def _restamp(rw, rel: str):
    """Simulate re-running recollindex for one outer file: rows take its current size, mtime, ctime."""
    path = rw.root / "roots" / "archive" / rel
    st = path.stat()
    for row in rw.index:
        if row.get("url") == f"file://{path}":
            row.update(sig=f"{st.st_size}{int(st.st_ctime)}", pcbytes=str(st.st_size), fmtime=f"0{int(st.st_mtime)}")
    rw.save()


def test_stale_index_keeps_version_and_freshness_apart(rw):
    rw.grant("archive", "search", "excerpt", "agent-context")
    paper = _paper(rw)
    assert paper["source"]["state"] == "fresh"
    cid = service.excerpt(rw.config, paper["occurrence_id"])["citation"]["citation_id"]
    source = rw.root / "roots/archive/2003/old-mail.zip"
    source.write_bytes(source.read_bytes() + b"synthetic change")
    described = service.describe(rw.config, paper["occurrence_id"])
    assert (described["state"], described["provider_version"], described["source"]) == ("changed", "same", "changed")
    assert described["indexed_source_stamp"]["size"] != described["live_source_stamp"]["size"]
    resolved = service.resolve_citation(rw.config, cid)
    assert resolved["state"] == "stale" and resolved["text_verified"] is False
    assert _paper(rw)["source"]["state"] == "changed"  # search still finds it, and says the index is stale
    packet = context.build(rw.config, "agent-context", occurrences=(paper["occurrence_id"],))
    assert packet["items"] == []
    assert packet["omitted"][0]["reason"] == "stale" and packet["omitted"][0]["source"] == "changed"
    assert corpus.PAPER_TEXT not in json.dumps(packet)


def test_restored_mtime_with_same_size_is_still_caught(rw):
    rw.grant("archive", "search", "excerpt")
    note = service.search(rw.config, "admin mode")["results"][0]
    path = rw.root / "roots/archive/notes/injection.md"
    st = path.stat()
    time.sleep(1.1)  # ctime has whole-second resolution in Recoll's sig
    path.write_text(corpus.INJECTION_TEXT.replace("admin", "ADMIN"))  # same size
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))  # mtime put back, as rsync -t or touch -r would
    assert path.stat().st_size == st.st_size and int(path.stat().st_mtime) == int(st.st_mtime)
    described = service.describe(rw.config, note["occurrence_id"])
    assert described["state"] == "changed" and described["source"] == "changed"
    with pytest.raises(service.Stale):
        service.excerpt(rw.config, note["occurrence_id"])


def test_missing_source_is_unavailable_everywhere(rw):
    rw.grant("archive", "search", "excerpt", "recover", "life-evidence")
    paper = _paper(rw)
    cid = service.excerpt(rw.config, paper["occurrence_id"])["citation"]["citation_id"]
    (rw.root / "roots/archive/2003/old-mail.zip").unlink()  # the index still lists it
    described = service.describe(rw.config, paper["occurrence_id"])
    assert (described["state"], described["source"]) == ("unavailable", "missing")
    with pytest.raises(Unavailable):
        service.excerpt(rw.config, paper["occurrence_id"])
    with pytest.raises(Unavailable):
        service.recover(rw.config, paper["occurrence_id"])
    assert service.resolve_citation(rw.config, cid)["state"] == "unavailable"
    packet = context.build(rw.config, "life-evidence", occurrences=(paper["occurrence_id"],))
    assert packet["omitted"][0]["reason"] == "unavailable" and packet["items"] == []


def test_unverifiable_source_returns_no_content(rw):
    rw.grant("archive", "search", "excerpt", "recover", "agent-context")
    for row in rw.index:
        if row.get("url", "").endswith("notes/injection.md"):
            for key in ("pcbytes", "fmtime", "fbytes"):
                row.pop(key, None)
    rw.save()
    note = service.search(rw.config, "admin mode")["results"][0]
    assert note["source"]["state"] == "unverifiable"
    assert service.describe(rw.config, note["occurrence_id"])["state"] == "unverifiable"
    with pytest.raises(service.Unverifiable):
        service.excerpt(rw.config, note["occurrence_id"])
    with pytest.raises(service.Unverifiable):
        service.recover(rw.config, note["occurrence_id"])
    item, = context.build(rw.config, "agent-context", occurrences=(note["occurrence_id"],))["items"]
    assert item["excerpt"] is None and item["excerpt_omitted_reason"].startswith("unverifiable")
    assert any("freshness cannot be checked" in n for n in item["uncertainty"])


def test_reindexing_a_scratch_source_supersedes_old_citations(rw):
    rw.grant("archive", "search", "excerpt", "recover")
    note = service.search(rw.config, "admin mode")["results"][0]
    old = service.excerpt(rw.config, note["occurrence_id"])["citation"]
    path = rw.root / "roots/archive/notes/injection.md"
    path.write_text("Rewritten meeting notes. admin mode was removed.\n")
    for row in rw.index:
        if row.get("url") == f"file://{path}":
            row["text"] = path.read_text()
    _restamp(rw, "notes/injection.md")
    # The index now holds a new version: the recorded occurrence is changed until it is found again.
    assert service.describe(rw.config, note["occurrence_id"])["provider_version"] == "changed"
    with pytest.raises(service.Stale):
        service.excerpt(rw.config, note["occurrence_id"])
    again = service.search(rw.config, "admin mode")["results"][0]
    assert again["occurrence_id"] == note["occurrence_id"] and again["version"] != note["version"]
    new = service.excerpt(rw.config, note["occurrence_id"])
    assert new["text"].startswith("Rewritten") and new["citation"]["citation_id"] != old["citation_id"]
    assert service.resolve_citation(rw.config, old)["state"] == "stale"
    assert service.resolve_citation(rw.config, new["citation"])["state"] == "current"
    assert Path(service.recover(rw.config, note["occurrence_id"])["recovered_to"]).read_text().startswith("Rewritten")


def test_source_changing_during_a_read_is_refused(rw, monkeypatch):
    rw.grant("archive", "search", "excerpt", "recover")
    paper = _paper(rw)
    source = rw.root / "roots/archive/2003/old-mail.zip"
    original_excerpt, original_recover = recoll_provider.Provider.excerpt, recoll_provider.Provider.recover

    def excerpt_then_change(self, *args):
        result = original_excerpt(self, *args)
        source.write_bytes(source.read_bytes() + b"during read")
        return result

    monkeypatch.setattr(recoll_provider.Provider, "excerpt", excerpt_then_change)
    with pytest.raises(service.Stale, match="while it was being read"):
        service.excerpt(rw.config, paper["occurrence_id"])
    _restamp(rw, "2003/old-mail.zip")
    paper = _paper(rw)
    monkeypatch.setattr(recoll_provider.Provider, "excerpt", original_excerpt)

    def recover_then_change(self, native_id, dest, *args):
        original_recover(self, native_id, dest, *args)
        source.write_bytes(source.read_bytes() + b"during recovery")

    monkeypatch.setattr(recoll_provider.Provider, "recover", recover_then_change)
    with pytest.raises(service.Stale, match="while it was being read"):
        service.recover(rw.config, paper["occurrence_id"])
    leftovers = [p for p in rw.config.files.recover_dir.rglob("*") if p.is_file()]
    assert leftovers == []
