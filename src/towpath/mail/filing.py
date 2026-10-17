"""Filing attachments: documents from the owner's mail, copied into Towpath's data folder.

``files/attachments/<account>/<year>/<sender>/<date> <file name>`` in the data folder (next to ``state/``), so they
sit on the owner's own server, organised by year and by who sent them. Documents only, as attachments.KINDS defines
them (PDF, Word, text, presentations); which kinds count may change with the owner.

Per account the owner chooses: off, a dry run (the default: Towpath lists what it would file, and where, and writes
nothing), or on. Filing reads each attachment through the read-only connection (BODY.PEEK, so nothing is marked read),
writes it, flushes it to disk, reads it back and checks its SHA-256. A message in several folders is filed once; a
document identical to one already filed is recorded as the same file, not copied again. Mail itself never changes.
"""

import hashlib
import os
import re
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from towpath.mail import attachments, settings
from towpath.stores import open_store, store_path

ROLE = "connect"
MODES = {"off": "Off", "dry-run": "Dry run: list what would be filed", "on": "File them"}
DEFAULT_MODE = "dry-run"
KINDS = ("documents",)
SLICE_SECONDS = 60
PLAN_BATCH = 500
SAFE = re.compile(r"[^A-Za-z0-9._@+ -]+")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def root(store_dir) -> Path:
    """files/attachments/ in the data folder (the stores' folder is the data folder's state/)."""
    return Path(store_dir).parent / "files" / "attachments"


def mode(store_dir, source_id: str, role: str = ROLE) -> str:
    return (settings._get(store_dir, source_id, "filing", role) or {}).get("mode", DEFAULT_MODE)


def set_mode(store_dir, source_id: str, value: str, author: str = "owner") -> None:
    if value not in MODES:
        raise ValueError("unknown filing mode")
    settings._set(store_dir, source_id, "filing", {"mode": value}, "mail-filing", author)


def _safe(text: str, limit: int) -> str:
    """A name that is one path component: no separators or control characters, never empty or a dot-name."""
    cleaned = SAFE.sub("_", text or "").strip(" .")[:limit]
    return cleaned or "_"


def planned_path(source_id: str, row: dict) -> str:
    """Where a document goes, relative to the attachments folder."""
    day = (row.get("date_utc") or "")[:10] or "undated"
    base = re.split(r"[/\\]", row.get("filename") or "")[-1] or "attachment"  # a sender's path means nothing here
    stem, dot, ext = base.rpartition(".")
    name = f"{_safe(stem, 100)}.{_safe(ext, 10)}" if dot and stem and ext.isalnum() else _safe(base, 110)
    return "/".join([_safe(source_id, 60), day[:4] if day != "undated" else "undated",
                     _safe(row.get("from_addr") or "unknown", 80), f"{day} {name}"])


def _candidates(store_dir, source_id: str, done: set, limit: int, role: str) -> list[dict]:
    if not store_path(store_dir, "source").exists():
        return []
    with closing(open_store(store_dir, "source", role)) as db:
        rows = attachments._rows(db, source_id, " AND kind IN (" + ",".join("?" for _ in KINDS) + ")", KINDS,
                                 "date_utc DESC")
    out = [r for r in rows if (r["message_key"], r["part_id"]) not in done]
    return out[:limit]


def _write(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "xb") as f:  # never over an existing file
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


def _unique(base: Path, rel: str) -> Path:
    target = base / rel
    n = 2
    while target.exists():
        stem, dot, ext = target.name.rpartition(".")
        target = target.with_name(f"{stem} ({n}).{ext}" if dot else f"{target.name} ({n})")
        n += 1
    return target


def file_pending(config, slice_seconds: float = SLICE_SECONDS, clock=time.monotonic) -> list[dict]:
    from towpath.adapters import build_connector
    from towpath.adapters.errors import SyncStop
    from towpath.adapters.imap import _mapped_errors
    from towpath.mail import analysis

    out = []
    deadline = clock() + slice_seconds
    base = root(config.store_dir)
    for sid in analysis.eligible(config):
        chosen = mode(config.store_dir, sid)
        if chosen == "off" or clock() >= deadline or not store_path(config.store_dir, "mail").exists():
            continue
        mail = open_store(config.store_dir, "mail", ROLE)
        connector = None
        planned = filed = same = failed = 0
        try:
            done = {(r[0], r[1]) for r in mail.execute("SELECT message_key, part_id FROM filed WHERE source_id = ? "
                                                       "AND status != 'planned'", (sid,))}
            plans = {(r[0], r[1]) for r in mail.execute("SELECT message_key, part_id FROM filed WHERE source_id = ? "
                                                        "AND status = 'planned'", (sid,))}
            todo = _candidates(config.store_dir, sid, done if chosen == "on" else done | plans, PLAN_BATCH, ROLE)
            if chosen == "dry-run":
                for r in todo:
                    mail.execute("INSERT OR REPLACE INTO filed VALUES (?,?,?,?,?,?,?,NULL,'planned',NULL,?)",
                                 (sid, r["message_key"], r["part_id"], r["item_id"], r["filename"],
                                  planned_path(sid, r), r["size"], now()))
                    planned += 1
                mail.commit()
            else:
                hashes = {r[0]: r[1] for r in mail.execute("SELECT sha256, path FROM filed WHERE source_id = ? AND "
                                                           "status = 'filed'", (sid,))}
                with _mapped_errors():
                    connector = build_connector(config.sources[sid], config)
                    for r in todo:
                        if clock() >= deadline:
                            break
                        status, detail, path, digest_ = "failed", None, None, None
                        try:
                            data = connector.fetch(r["native_id"], r["part_id"])
                            digest_ = hashlib.sha256(data).hexdigest()
                            if digest_ in hashes:
                                status, path = "same", hashes[digest_]
                            else:
                                target = _unique(base, planned_path(sid, r))
                                if base.resolve() not in target.resolve().parents:
                                    raise ValueError("outside the attachments folder")
                                _write(target, data)
                                if hashlib.sha256(target.read_bytes()).hexdigest() != digest_:
                                    detail = "the copy didn't match what the server sent"
                                    target.unlink()
                                else:
                                    status, path = "filed", str(target.relative_to(base))
                                    hashes[digest_] = path
                        except SyncStop:
                            raise
                        except Exception as exc:  # noqa: BLE001 - one attachment's problem is recorded, the rest go on
                            detail = f"couldn't be filed ({type(exc).__name__})"
                        mail.execute("INSERT OR REPLACE INTO filed VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                                     (sid, r["message_key"], r["part_id"], r["item_id"], r["filename"],
                                      path or planned_path(sid, r), r["size"], digest_, status, detail, now()))
                        mail.commit()
                        filed += status == "filed"
                        same += status == "same"
                        failed += status == "failed"
        except SyncStop as exc:
            out.append({"source_id": sid, "step": "mail-filing", "stopped": exc.reason})
            continue
        finally:
            mail.close()
            if connector is not None:
                connector.close()
        if planned or filed or same or failed:
            out.append({"source_id": sid, "step": "mail-filing", "planned": planned, "filed": filed, "same": same,
                        "failed": failed})
    return out


def summary(store_dir, source_id: str, role: str = "web") -> dict:
    """How many documents are planned, filed, the same as one filed, or failed; and the latest."""
    if not store_path(store_dir, "mail").exists():
        return {"counts": {}, "latest": [], "folder": str(root(store_dir))}
    with closing(open_store(store_dir, "mail", role)) as db:
        counts = {r[0]: r[1] for r in db.execute("SELECT status, count(*) FROM filed WHERE source_id = ? GROUP BY 1",
                                                (source_id,))}
        latest = [dict(r) for r in db.execute("SELECT * FROM filed WHERE source_id = ? ORDER BY at DESC, path "
                                              "LIMIT 20", (source_id,))]
    return {"counts": counts, "latest": latest, "folder": str(root(store_dir))}
