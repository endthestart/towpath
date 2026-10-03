"""Native evaluation harness and the bounded tool runner. Native cases skip when a tool is missing."""

import json
import shutil
import subprocess
import sys

import pytest

from towpath.discovery import evaluate as ev
from towpath.discovery.run import ToolError, run


def test_runner_enforces_time_output_and_presence():
    assert run(["echo", "ok"], timeout=5, max_output=100).stdout == b"ok\n"
    with pytest.raises(ToolError) as slow:
        run([sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.5, max_output=100)
    assert slow.value.code == "timeout"
    with pytest.raises(ToolError) as noisy:
        run([sys.executable, "-c", "print('x' * 100000)"], timeout=10, max_output=1000)
    assert noisy.value.code == "output-limit"
    with pytest.raises(ToolError) as absent:
        run(["towpath-no-such-tool"], timeout=1, max_output=10)
    assert absent.value.code == "not-installed"
    with pytest.raises(ToolError) as shell:
        run("echo hi; rm -rf /", timeout=1, max_output=10)
    assert shell.value.code == "bad-command"


def test_missing_tools_are_reported_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    report = ev.evaluate(tmp_path / "out", timeout=10)
    assert report["tools"]["recoll"] == {"available": False, "reason": "recollindex is not installed"}
    assert report["tools"]["sist2"]["available"] is False
    text = (tmp_path / "out" / "report.json").read_text()
    assert str(tmp_path) not in text and json.loads(text)["corpus"]["synthetic"] is True
    assert [p.name for p in (tmp_path / "out").iterdir()] == ["report.json"]


def _recoll_ready() -> bool:
    return shutil.which("recollindex") is not None and ev.find_recoll_python(timeout=10) is not None


def _sist2_ready() -> bool:
    if shutil.which("sist2"):
        return True
    if not shutil.which("docker"):
        return False
    probe = subprocess.run(["docker", "image", "inspect", ev.SIST2_IMAGE], capture_output=True)
    return probe.returncode == 0


@pytest.mark.skipif(not _recoll_ready(), reason="Recoll and its Python binding are not installed")
def test_native_recoll_finds_and_recovers_the_nested_paper(tmp_path):
    result = ev.evaluate(tmp_path, tools=("recoll",), timeout=120)["tools"]["recoll"]
    hits = result["cases"]["nested-attachment"]["hits"]
    assert "archive/2003/old-mail.zip | mail/backup.mbox:1:0" in hits
    assert "archive/2003/old-mail.zip | mail/backup.mbox:2:0" in hits
    nested = result["nested"]
    assert nested["recovered_matches_sha256"] and nested["dmtime_is_message_date"]
    assert nested["excerpt_contains_paper_text"]
    assert result["cases"]["encrypted-member"]["hits"] == []
    assert result["cases"]["symlink-escape"]["hits"] == []


@pytest.mark.skipif(not _sist2_ready(), reason="sist2 (binary or local image) is not installed")
def test_native_sist2_reports_lineage_and_text_limit(tmp_path):
    result = ev.evaluate(tmp_path, tools=("sist2",), timeout=300)["tools"]["sist2"]
    hits = result["cases"]["nested-attachment"]["hits"]
    assert "archive/2003/old-mail.zip#/mail/backup.mbox#/message-0.eml#/paper.docx" in hits
    assert result["long_document_content_bytes"] == 32768
    assert result["cases"]["beyond-text-limit"]["hits"] == []
