"""Native evaluation: run installed providers over the same generated corpus.

Generates the synthetic corpus in a temporary folder under ``out``, indexes it
with each tool that is installed, asks the same questions, and writes
``report.json``. Every call has a time limit. A missing tool is reported as
unavailable; neither tool is a dependency of Towpath. The report holds only
paths relative to the corpus, so it carries nothing private, and its numbers
describe the synthetic corpus only.
"""

import hashlib
import json
import os
import re
import resource
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from towpath.discovery import corpus
from towpath.discovery.run import ToolError, run

SIST2_IMAGE = "sist2app/sist2:4.2.3"
BRIDGE = Path(__file__).parent / "providers" / "recoll_bridge.py"
PYTHON_CANDIDATES = ("python3", "/usr/bin/python3", "/usr/bin/python3.12", "/usr/bin/python3.11")

# (case, query, what an ideal result would contain, what the case tests)
CASES = [
    ("nested-attachment", "zebrafinch economy", "archive/2003/old-mail.zip > message > paper.docx (twice)",
     "ZIP > mbox > message > attachment lineage; duplicates"),
    ("message-body", "canal cafe", "archive/2003/old-mail.zip > message 3", "mail body text inside a ZIP"),
    ("beyond-text-limit", "kingfisher", "archive/long/thesis-notes.txt (line after 54 KB)",
     "text extraction limits"),
    ("encrypted-member", "encrypted archive", "nothing without the passphrase", "encrypted archives"),
    ("traversal-names", "traversal member", "archive/tricky/traversal.zip members", "hostile member names"),
    ("symlink-escape", "outside the root", "nothing (Towpath refuses it anyway)", "symlinks leaving the root"),
    ("excluded-path", "Private diary", "indexed by the tool; Towpath must filter it", "exclusions are Towpath's"),
    ("prompt-injection", "admin mode", "archive/notes/injection.md, as plain text", "injection text is data"),
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _rss_kb() -> int:
    return resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss


def _size(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def _words_match(query: str, text: str) -> bool:
    text = text.lower()
    return all(w.lower() in text for w in query.split())


# ---------------------------------------------------------------- Recoll


def _bridge(python: str, request: dict, timeout: float) -> dict:
    result = run([python, str(BRIDGE)], input_bytes=json.dumps(request).encode(), timeout=timeout,
                 max_output=16_000_000, env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": "/nonexistent"})
    return json.loads(result.stdout or b"{}")


def find_recoll_python(timeout: float = 20, candidates=PYTHON_CANDIDATES) -> str | None:
    for candidate in (sys.executable, *candidates):
        try:
            if _bridge(candidate, {"op": "probe"}, timeout).get("binding"):
                return candidate
        except (ToolError, ValueError):
            continue
    return None


def _recoll_location(row: dict, roots: Path) -> str:
    path = row["url"][len("file://"):] if row["url"].startswith("file://") else row["url"]
    rel = os.path.relpath(path, roots)
    return f"{rel} | {row['ipath']}" if row.get("ipath") else rel


def evaluate_recoll(corpus_dir: Path, work: Path, timeout: float, python: str | None = None) -> dict:
    if shutil.which("recollindex") is None:
        return {"available": False, "reason": "recollindex is not installed"}
    roots = corpus_dir / "roots"
    conf = work / "recoll-conf"
    conf.mkdir(parents=True)
    (conf / "recoll.conf").write_text(f"topdirs = {roots}\n")
    report: dict = {"available": True, "interface": "recollindex CLI + Python binding via recoll_bridge.py",
                    "config": "topdirs only; every other setting at Recoll's default"}
    try:
        version = run(["recollindex", "-c", str(conf), "-V"], timeout=timeout, max_output=10_000)
        found = re.search(r"Recoll version: (.+)", version.stderr_tail + version.stdout.decode(errors="replace"))
        report["version"] = found.group(1).strip() if found else "unknown"
        rss_before = _rss_kb()
        index = run(["recollindex", "-c", str(conf)], timeout=timeout, max_output=10_000_000)
        report["index"] = {"seconds": round(index.seconds, 2), "returncode": index.returncode,
                           "index_bytes": _size(conf / "xapiandb") if (conf / "xapiandb").exists() else None,
                           "peak_child_rss_kb": max(_rss_kb(), rss_before),
                           "rss_note": "largest child process of this harness so far (ru_maxrss)"}
    except ToolError as exc:
        report.update(available=False, reason=f"{exc.code}: {exc.detail}")
        return report
    missing = conf / "missing"
    report["missing_helpers"] = missing.read_text().strip().splitlines() if missing.exists() else []

    python = python or find_recoll_python()
    if python is None:
        report["binding"] = {"available": False, "reason": "no interpreter here can import Recoll's Python binding"}
        return report
    report["binding"] = {"available": True, "interpreter": os.path.basename(python)}
    base = {"confdir": str(conf)}
    try:
        rows = _bridge(python, {**base, "op": "enumerate", "dir": str(roots), "max_rows": 1000}, timeout)["rows"]
        report["documents"] = sorted(f"{_recoll_location(r, roots)} [{r['mtype']}]" for r in rows)
        report["cases"] = {}
        paper_row = None
        for case, query, _, _ in CASES:
            started = time.monotonic()
            hits = _bridge(python, {**base, "op": "search", "query": query,
                                    "dirs": [str(roots / "archive"), str(roots / "shared")], "max_rows": 50},
                           timeout)["rows"]
            report["cases"][case] = {"query": query, "seconds": round(time.monotonic() - started, 3),
                                     "hits": [_recoll_location(h, roots) for h in hits]}
            if case == "nested-attachment":
                paper_row = next((h for h in hits if h.get("filename") == "paper.docx" and h.get("ipath")), None)
        report["nested"] = _recoll_nested(python, base, paper_row, work, timeout)
    except (ToolError, KeyError, ValueError) as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    return report


def _recoll_nested(python: str, base: dict, row: dict | None, work: Path, timeout: float) -> dict:
    if row is None:
        return {"found": False}
    paper_sha = hashlib.sha256(corpus.docx(corpus.PAPER_TEXT)).hexdigest()
    dest = work / "recovered-paper.docx"
    out = {"found": True, "ipath": row["ipath"], "fmtime": row.get("fmtime"), "dmtime": row.get("dmtime"),
           "dmtime_is_message_date": str(row.get("dmtime", "")).lstrip("0") == "1052128800",
           "sig": row.get("sig"), "fbytes": row.get("fbytes"), "dbytes": row.get("dbytes")}
    rec = _bridge(python, {**base, "op": "recover", "udi": row["rcludi"], "dest": str(dest), "max_bytes": 10**7},
                  timeout)
    out["recovered"] = bool(rec.get("written")) and dest.exists()
    out["recovered_matches_sha256"] = out["recovered"] and hashlib.sha256(dest.read_bytes()).hexdigest() == paper_sha
    ex = _bridge(python, {**base, "op": "excerpt", "udi": row["rcludi"], "start": 0, "max_chars": 500}, timeout)
    out["excerpt_contains_paper_text"] = corpus.PAPER_TEXT[:40] in ex.get("text", "")
    out["excerpt_total_bytes"] = ex.get("total_bytes")
    return out


# ---------------------------------------------------------------- sist2


def _sist2_prefix(work: Path, roots: Path, binary: str | None, image: str | None, timeout: float):
    """(argv prefix, roots as the tool sees them, work folder as the tool sees it, how it runs)."""
    if binary or (image is None and shutil.which("sist2")):
        return [binary or "sist2"], str(roots), str(work), "binary"
    image = image or SIST2_IMAGE
    if shutil.which("docker") is None:
        return None, None, None, "sist2 binary not found and docker is not installed"
    try:
        probe = run(["docker", "image", "inspect", image], timeout=timeout, max_output=10_000_000)
    except ToolError as exc:
        return None, None, None, f"docker unusable ({exc.code})"
    if probe.returncode != 0:
        return None, None, None, f"sist2 binary not found and image {image} is not present locally"
    prefix = ["docker", "run", "--rm", "--network", "none", "-v", f"{roots}:/roots:ro", "-v", f"{work}:/work", image]
    return prefix, "/roots", "/work", f"docker image {image}"


def evaluate_sist2(corpus_dir: Path, work: Path, timeout: float, binary: str | None = None,
                   image: str | None = None) -> dict:
    roots = corpus_dir / "roots"
    s2 = work / "sist2"
    s2.mkdir(parents=True)
    prefix, roots_arg, work_arg, how = _sist2_prefix(s2, roots, binary, image, timeout)
    if prefix is None:
        return {"available": False, "reason": how}
    report: dict = {"available": True, "runs_as": how,
                    "interface": "sist2 scan + index --print (NDJSON); matching below is the harness's own "
                                 "substring test over extracted text, not sist2's search"}
    try:
        version = run(prefix + ["--version"], timeout=timeout, max_output=10_000)
        report["version"] = version.stdout.decode(errors="replace").strip().splitlines()[-1:] or ["unknown"]
        report["version"] = report["version"][0]
        rss_before = _rss_kb()
        scan = run(prefix + ["scan", roots_arg, "-o", f"{work_arg}/idx.sist2"], timeout=timeout,
                   max_output=10_000_000)
        report["index"] = {"seconds": round(scan.seconds, 2), "returncode": scan.returncode,
                           "index_bytes": _size(s2), "options": "defaults (content-size 32768, archive recurse)",
                           "peak_child_rss_kb": None if how.startswith("docker") else max(_rss_kb(), rss_before)}
        printed = run(prefix + ["index", "--print", f"{work_arg}/idx.sist2"], timeout=timeout,
                      max_output=64_000_000)
    except ToolError as exc:
        report.update(available=False, reason=f"{exc.code}: {exc.detail}")
        return report
    docs = []
    for line in printed.stdout.decode(errors="replace").splitlines():
        if line.startswith("{"):
            try:
                docs.append(json.loads(line)["_source"])
            except (ValueError, KeyError):
                continue
    def location(d):  # noqa: E306
        name = d.get("name", "") + (f".{d['extension']}" if d.get("extension") else "")
        return f"{d['path']}/{name}" if d.get("path") else name
    report["documents"] = sorted(f"{location(d)} [{d.get('mime')}]" for d in docs)
    report["cases"] = {}
    for case, query, _, _ in CASES:
        hits = [location(d) for d in docs if _words_match(query, d.get("content", "") + " " + location(d))]
        report["cases"][case] = {"query": query, "hits": hits}
    long = next((d for d in docs if d.get("name") == "thesis-notes"), None)
    report["long_document_content_bytes"] = len(long.get("content", "").encode()) if long else None
    paper = next((d for d in docs if d.get("name") == "paper" and "#/" in d.get("path", "")), None)
    report["nested"] = {"found": paper is not None, "path": paper and paper["path"],
                        "mtime": paper and paper.get("mtime"),
                        "recovery": "not evaluated: no documented CLI to extract a nested original"}
    return report


# ---------------------------------------------------------------- driver


def evaluate(out: Path, tools=("recoll", "sist2"), timeout: float = 120, recoll_python: str | None = None,
             sist2_binary: str | None = None, sist2_image: str | None = None, keep: bool = False) -> dict:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="native-eval-", dir=out))
    try:
        corpus_dir = work / "corpus"
        summary = corpus.generate(corpus_dir)
        roots = corpus_dir / "roots"
        report = {
            "format": "towpath.files.native-eval/1", "generated_at": _now(), "timeout_seconds": timeout,
            "corpus": {"files": sorted(os.path.relpath(p, roots) for p in roots.rglob("*")
                                       if p.is_file() or p.is_symlink()),
                       "paper_sha256": summary["paper_sha256"], "synthetic": True},
            "cases": [{"case": c, "query": q, "ideal": i, "tests": t} for c, q, i, t in CASES],
            "tools": {},
        }
        if "recoll" in tools:
            report["tools"]["recoll"] = evaluate_recoll(corpus_dir, work, timeout, recoll_python)
        if "sist2" in tools:
            report["tools"]["sist2"] = evaluate_sist2(corpus_dir, work, timeout, sist2_binary, sist2_image)
    finally:
        if not keep:
            shutil.rmtree(work, ignore_errors=True)
    text = json.dumps(report, indent=2, sort_keys=True)
    (out / "report.json").write_text(text)
    return report
