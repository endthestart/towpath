"""Running recollindex: its configuration, its status, and starting and stopping it."""

import json
import os
import shutil
import signal
import subprocess
from pathlib import Path

from towpath.folders.choices import DEFAULTS, options


def write_conf(confdir: Path, roots: list[dict], scratch: Path, settings: dict | None = None) -> Path:
    from towpath.photos import PRIVATE_GLOBS, is_private

    o = {**DEFAULTS, **(settings or {})}
    confdir.mkdir(parents=True, exist_ok=True)
    # Recoll never goes into the owner's private photo export: not as a chosen folder, nor inside one.
    topdirs = " ".join(json.dumps(r["path"]) for r in roots if not is_private(r["path"]))
    skipped = " ".join(json.dumps(g) for g in PRIVATE_GLOBS)
    conf = confdir / "recoll.conf"
    conf.write_text(f"""# Written by Towpath from the Folders page (folders and indexing settings); edits are replaced.
topdirs = {topdirs}
skippedPaths = {skipped}
skippedNames = {o["skip"]}
noContentSuffixes+ = {o["name_only"]}
usesystemfilecommand = {int(bool(o["sniff"]))}
thrQSizes = 2 2 2
thrTCounts = {o["threads"]} {max(1, o["threads"] // 2)} 1
followLinks = 0
indexallfilenames = 1
textfilemaxmbs = {o["text_limit_mb"]}
compressedfilemaxkbs = {o["compressed_limit_mb"] * 1000}
idxflushmb = {o["batch_mb"]}
pdfocr = 0
loglevel = 2
""")
    return conf


def read_status(confdir: Path) -> dict:
    """Counts from Recoll's own status file. The current file name is never kept."""
    values = {}
    try:
        for line in (confdir / "idxstatus.txt").read_text().splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() in {"phase", "docsdone", "filesdone", "fileerrors", "dbtotdocs", "totfiles"}:
                values[key.strip()] = int(value.strip()) if value.strip().lstrip("-").isdigit() else value.strip()
    except OSError:
        pass
    return values


def _command(confdir: Path) -> list[str]:
    cmd = ["recollindex", "-c", str(confdir), "-k"]  # -k: retry files that failed before (a helper may now exist)
    if shutil.which("ionice"):
        cmd = ["ionice", "-c", "3"] + cmd  # idle I/O class where the kernel schedules I/O; ZFS ignores it
    return ["nice", "-n", "10"] + cmd if shutil.which("nice") else cmd


def _start(c: dict, data_dir: Path) -> subprocess.Popen:
    confdir, scratch = data_dir / "index" / "recoll", data_dir / "scratch" / "recoll"
    scratch.mkdir(parents=True, exist_ok=True)
    write_conf(confdir, c["settings"]["roots"], scratch, options(c))
    env = {**os.environ, "HOME": str(confdir), "TMPDIR": str(scratch), "RECOLL_TMPDIR": str(scratch)}
    log = open(confdir / "recollindex.log", "w")
    return subprocess.Popen(_command(confdir), stdout=log, stderr=subprocess.STDOUT, env=env,
                            start_new_session=True)


def _stop(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        os.killpg(proc.pid, signal.SIGTERM)  # Recoll flushes what it has indexed and exits
        try:
            proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
