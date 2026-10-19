#!/usr/bin/env python3
"""Consistent dumps of Towpath's SQLite stores, for a host's nightly backup job (Python standard library only).

Each run takes a ZFS snapshot of Towpath's data folder, so the live stores are never locked; copies each store (and
its journal, if a write was under way) out of the snapshot into a work folder; opens the copy, which rolls back any
unfinished write exactly as SQLite would after a crash; checks it with ``PRAGMA integrity_check``; writes a compact
copy with ``VACUUM INTO``; and gzips it into ``<dest>/<stamp>/`` with a manifest (sizes, SHA-256, checks). Then it
removes the snapshot and the work folder, and prunes old dumps.

Left out on purpose: ``credentials/`` (passwords stay out of backups: re-enter them after a restore),
``thumbs.db`` (photo previews, rebuilt by the worker) and the Recoll index (rebuilt by indexing). ``files.db``
(the file catalog, large and slow to rebuild) is dumped on the weekly run only.

    towpath-dump.py --dataset zssd/appfast/towpath --mount /mnt/zssd/appfast/towpath \
        --dest /mnt/tank/storage/backup/db-dumps/towpath [--weekly]
    towpath-dump.py --verify --dest ...      # restore check of the newest dump
    towpath-dump.py --source DIR --dest ...  # no snapshot: DIR is already a stopped or snapshot copy (tests)
"""

import argparse
import gzip
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

SKIP = {"thumbs.db"}
WEEKLY_ONLY = {"files.db"}
FILES = ("web-login.json",)  # the sign-in (a password hash), so a restore doesn't need a new account
KEEP_DAILY, KEEP_WEEKLY, KEEP_MONTHLY = 14, 8, 6


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def private(path: Path, mode: int) -> None:
    """Owner-only modes where the filesystem allows them; a dataset with restricted NFSv4 ACLs refuses chmod and
    keeps its inherited ACL instead (TrueNAS backup datasets)."""
    try:
        os.chmod(path, mode)
    except PermissionError:
        pass


def gz(src: Path, dest: Path) -> None:
    with src.open("rb") as f, gzip.open(dest, "wb", compresslevel=6) as g:
        shutil.copyfileobj(f, g, 1 << 20)


def dump_store(snap_db: Path, work: Path, out: Path) -> dict:
    """One store from the snapshot: copied with its journal, rolled back if needed, checked, compacted, gzipped."""
    copy = work / snap_db.name
    shutil.copyfile(snap_db, copy)
    journal = snap_db.with_name(snap_db.name + "-journal")
    if journal.exists():
        shutil.copyfile(journal, work / journal.name)  # opening the copy rolls the unfinished write back
    con = sqlite3.connect(copy)
    try:
        check = con.execute("PRAGMA integrity_check").fetchone()[0]
        if check != "ok":
            raise RuntimeError(f"{snap_db.name}: integrity check failed: {check[:200]}")
        compact = work / (snap_db.stem + ".compact.db")
        con.execute("VACUUM INTO ?", (str(compact),))
    finally:
        con.close()
    target = out / (snap_db.name + ".gz")
    gz(compact, target)
    entry = {"store": snap_db.name, "bytes": compact.stat().st_size, "gz": target.name,
             "gz_bytes": target.stat().st_size, "gz_sha256": sha256(target), "integrity": "ok"}
    for p in (copy, compact, work / journal.name):
        p.unlink(missing_ok=True)
    return entry


def run(source: Path, dest: Path, weekly: bool, stamp: str) -> Path:
    out = dest / stamp
    out.mkdir(parents=True, exist_ok=False)
    private(out, 0o700)
    work = Path(tempfile.mkdtemp(prefix=".work-", dir=dest))
    manifest = {"format": "towpath.db-dump/1", "stamp": stamp, "weekly": weekly, "stores": [], "files": [],
                "left_out": ["credentials/", "thumbs.db", "Recoll index"] + ([] if weekly else ["files.db"])}
    try:
        for db in sorted((source / "state").glob("*.db")):
            if db.name in SKIP or (db.name in WEEKLY_ONLY and not weekly):
                continue
            manifest["stores"].append(dump_store(db, work, out))
        for name in FILES:
            f = source / "state" / name
            if f.exists():
                shutil.copyfile(f, out / name)
                manifest["files"].append({"file": name, "sha256": sha256(out / name)})
        config = source / "config" / "towpath.toml"
        if config.exists():
            shutil.copyfile(config, out / "towpath.toml")
            manifest["files"].append({"file": "towpath.toml", "sha256": sha256(out / "towpath.toml")})
        manifest["finished"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
        for p in out.iterdir():
            private(p, 0o600)
    except BaseException:
        shutil.rmtree(out, ignore_errors=True)  # never leave a half dump that looks whole
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return out


def prune(dest: Path, today: datetime) -> list[str]:
    """Keep the last 14 daily dumps, the last 8 weekly ones and the first dump of each of the last 6 months."""
    dumps = sorted(p for p in dest.iterdir() if p.is_dir() and not p.name.startswith(".") and
                   (p / "manifest.json").exists())
    keep = set(dumps[-KEEP_DAILY:])
    weekly = [p for p in dumps if json.loads((p / "manifest.json").read_text()).get("weekly")]
    keep |= set(weekly[-KEEP_WEEKLY:])
    months: dict[str, Path] = {}
    for p in dumps:
        months.setdefault(p.name[:6], p)
    keep |= set(sorted(months.values())[-KEEP_MONTHLY:])
    gone = []
    for p in dumps:
        if p not in keep:
            shutil.rmtree(p)
            gone.append(p.name)
    return gone


def verify(dest: Path) -> dict:
    """Restore check of the newest dump: every file matches its manifest, and every store decompresses, opens and
    passes ``PRAGMA integrity_check`` in a throwaway folder."""
    dumps = sorted(p for p in dest.iterdir() if p.is_dir() and (p / "manifest.json").exists())
    if not dumps:
        raise RuntimeError("no dump to check")
    newest = dumps[-1]
    manifest = json.loads((newest / "manifest.json").read_text())
    with tempfile.TemporaryDirectory(prefix=".restore-", dir=dest) as tmp:
        for s in manifest["stores"]:
            gzfile = newest / s["gz"]
            if sha256(gzfile) != s["gz_sha256"]:
                raise RuntimeError(f"{s['gz']}: checksum differs from the manifest")
            db = Path(tmp) / s["store"]
            with gzip.open(gzfile, "rb") as g, db.open("wb") as f:
                shutil.copyfileobj(g, f, 1 << 20)
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            try:
                if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError(f"{s['store']}: restored copy fails its integrity check")
                con.execute("SELECT count(*) FROM sqlite_master").fetchone()
            finally:
                con.close()
            db.unlink()
        for f in manifest["files"]:
            if sha256(newest / f["file"]) != f["sha256"]:
                raise RuntimeError(f"{f['file']}: checksum differs from the manifest")
    return {"dump": newest.name, "stores": len(manifest["stores"]), "restore_check": "ok"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dest", required=True, type=Path)
    ap.add_argument("--dataset")
    ap.add_argument("--mount", type=Path)
    ap.add_argument("--source", type=Path)
    ap.add_argument("--weekly", action="store_true")
    ap.add_argument("--verify", action="store_true")
    args = ap.parse_args()
    args.dest.mkdir(parents=True, exist_ok=True)
    if args.verify:
        print(json.dumps(verify(args.dest)))
        return 0
    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%dT%H%MZ")
    snapshot = None
    if args.source:
        source = args.source
    else:
        if not (args.dataset and args.mount):
            ap.error("give --dataset and --mount (or --source)")
        snapshot = f"{args.dataset}@towpath-dump-{stamp}"
        subprocess.run(["zfs", "snapshot", snapshot], check=True)
        source = args.mount / ".zfs" / "snapshot" / f"towpath-dump-{stamp}"
    try:
        out = run(source, args.dest, args.weekly, stamp)
    finally:
        if snapshot:
            subprocess.run(["zfs", "destroy", snapshot], check=False)
    gone = prune(args.dest, now)
    print(json.dumps({"dump": out.name, "pruned": gone}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
