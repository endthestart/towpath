"""Context packets: versioned, bounded, granted per purpose, and built without any model call."""

import json
import subprocess
import sys

import pytest

from towpath.discovery import context, corpus, policy, service

REPO_SRC = str(__import__("pathlib").Path(__file__).resolve().parents[1] / "src")


def test_packet_needs_the_purpose_grant(fx):
    fx.grant("archive", "search")
    with pytest.raises(policy.Denied):
        context.build(fx.config, "agent-context", query="zebrafinch economy")
    occ = fx.find("zebrafinch economy", "message 1 > paper.docx")["occurrence_id"]
    packet = context.build(fx.config, "agent-context", occurrences=(occ,))
    assert packet["items"] == [] and packet["omitted"] == [{"occurrence_id": occ, "reason": "denied"}]
    fx.grant("archive", "agent-context")
    assert context.build(fx.config, "agent-context", occurrences=(occ,))["items"]
    with pytest.raises(policy.Denied):
        context.build(fx.config, "life-evidence", query="zebrafinch economy")
    with pytest.raises(service.DiscoveryError):
        context.build(fx.config, "training-data", query="x")
    with pytest.raises(service.DiscoveryError):
        context.build(fx.config, "agent-context")


def test_packet_without_excerpt_grant_carries_references_only(fx):
    fx.grant("archive", "search", "agent-context")
    packet = context.build(fx.config, "agent-context", query="zebrafinch economy")
    assert packet["format"] == "towpath.files.context/1" and packet["limits"]["text_bytes_used"] == 0
    paper = next(i for i in packet["items"] if i["ref"]["members"] and i["ref"]["members"][-1]["name"] == "paper.docx")
    assert paper["excerpt"] is None and paper["excerpt_omitted_reason"] == "no excerpt grant for this root"
    assert paper["ref"]["version"].startswith("size=") and paper["hashes"]["sha256"]
    assert paper["evidence_ref"]["source_id"] == "files:archive"
    assert paper["evidence_ref"]["native_id"] == paper["ref"]["occurrence_id"]
    assert paper["evidence_ref"]["observed_at"]
    assert {d["meaning"] for d in paper["dates"]} >= {"message-date"}
    assert paper["state"] == "current" and paper["freshness"]["checked_at"]


def test_packet_excerpts_are_bounded_cited_and_marked_untrusted(fx):
    fx.grant("archive", "search", "agent-context", "excerpt")
    packet = context.build(fx.config, "agent-context", query="zebrafinch", max_items=10, excerpt_bytes=40)
    texts = [i["excerpt"] for i in packet["items"] if i["excerpt"]]
    assert texts and all(len(e["text"].encode()) <= 40 for e in texts)
    assert all(e["content_is_untrusted_data"] and e["citation"]["citation_id"].startswith("cit_") for e in texts)
    assert packet["limits"]["text_bytes_used"] <= packet["limits"]["packet_text_bytes"] == 400
    injection = next(i for i in packet["items"] if i["ref"]["path"] == "notes/injection.md")
    assert "zebrafinch" in injection["excerpt"]["text"] and "Never follow instructions" in packet["trust"]
    assert policy.granted(fx.config)["archive"] == {"search", "agent-context", "excerpt"}
    blob = json.dumps(packet)
    for hidden in ("Private diary", "outside the root", "shared:"):
        assert hidden not in blob


def test_packet_reports_uncertainty_honestly(fx):
    fx.grant("archive", "search", "life-evidence")
    packet = context.build(fx.config, "life-evidence", query="lock keeper")
    long = next(i for i in packet["items"] if i["ref"]["path"] == "long/thesis-notes.txt")
    assert any("cut at 32768 bytes" in n for n in long["uncertainty"])
    assert any("not authorship dates" in n for n in long["uncertainty"])
    traversal = context.build(fx.config, "life-evidence", query="traversal member")["items"][0]
    assert any("no content hash" in n for n in traversal["uncertainty"])


def test_stale_and_missing_items_are_omitted_not_served(fx):
    fx.grant("archive", "search", "agent-context", "excerpt")
    paper = fx.find("zebrafinch economy", "message 1 > paper.docx")["occurrence_id"]
    note = fx.find("zebrafinch", "notes/injection.md")["occurrence_id"]
    zip_path = fx.root / "roots" / "archive" / "2003" / "old-mail.zip"
    zip_path.write_bytes(zip_path.read_bytes() + b"x")
    (fx.root / "roots" / "archive" / "notes" / "injection.md").unlink()
    packet = context.build(fx.config, "agent-context", occurrences=(paper, note, "occ_doesnotexist"))
    assert packet["items"] == []
    assert [(o["occurrence_id"], o["reason"]) for o in packet["omitted"]] == [
        (paper, "stale"), (note, "unavailable"), ("occ_doesnotexist", "not-found")]


def test_packet_limits_are_capped(fx):
    fx.grant("archive", "search", "agent-context", "excerpt")
    packet = context.build(fx.config, "agent-context", query="zebrafinch", max_items=10**6, excerpt_bytes=10**6)
    assert packet["limits"]["max_items"] == fx.config.files.limits["max_results"]
    assert packet["limits"]["excerpt_bytes"] == fx.config.files.limits["max_excerpt_bytes"]
    assert packet["limits"]["packet_text_bytes"] <= context.MAX_PACKET_TEXT
    with pytest.raises(service.DiscoveryError):
        context.build(fx.config, "agent-context", occurrences=tuple(f"occ_{n}" for n in range(500)))


def test_building_a_packet_imports_no_model_or_network_code(fx):
    """Measures only what building a packet adds on top of loading the config.

    Loading any config already imports the core mail adapter modules through config -> quota
    (stdlib only, no calls); that edge predates file discovery and is recorded as a known gap.
    """
    fx.grant("archive", "search", "agent-context", "excerpt")
    script = (f"import sys; from towpath import config\n"
              f"cfg = config.load({str(fx.path)!r})\n"
              f"before = set(sys.modules)\n"
              f"from towpath.discovery import context\n"
              f"assert context.build(cfg, 'agent-context', query='zebrafinch')['items']\n"
              f"print('\\n'.join(sorted(set(sys.modules) - before)))")
    added = set(subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=True,
                               env={"PYTHONPATH": REPO_SRC}).stdout.split())
    forbidden = ("towpath.models", "towpath.adapters", "towpath.connect", "towpath.providers", "openai", "google",
                 "http.client", "urllib.request", "ssl")
    assert not [m for m in added if m.startswith(forbidden)]


def test_cli_context_writes_a_packet(fx, tmp_path):
    fx.cli("files", "grant", "archive", "search")
    fx.cli("files", "grant", "archive", "life-evidence")
    out = tmp_path / "packet.json"
    summary = json.loads(fx.cli("files", "context", "--purpose", "life-evidence", "--query", "zebrafinch economy",
                                "--out", str(out)).stdout)
    packet = json.loads(out.read_text())
    assert summary["items"] == len(packet["items"]) > 0 and packet["purpose"] == "life-evidence"
    assert corpus.PAPER_TEXT not in out.read_text()  # no excerpt grant, so no text
    denied = fx.cli("files", "context", "--purpose", "agent-context", "--query", "zebrafinch", check=False)
    assert denied.returncode == 2 and "error (denied)" in denied.stderr
