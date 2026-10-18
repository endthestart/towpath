"""Keeping the evidence: optional, off until the owner turns it on.

For each event the owner kept (accepted or corrected) whose evidence is a message, the worker keeps a copy of that
message's text, with its date, sender and subject, in Towpath's data folder (``evidence/``), so the event keeps its
evidence if the mail is later deleted from the server. The text is what mail management already read when it has,
else the message's text part read without marking it read. Nothing on the mail server changes. Copies follow the
owner's decisions: an event no longer kept, or a source no longer allowed for the life story, loses its copy, and
turning keeping off deletes every copy.
"""

import hashlib
import json
import os
import tempfile
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from towpath import life
from towpath.stores import open_store, store_path

TARGET, KEY = "life:evidence", "keep"
FOLDER = "evidence"
SLICE_SECONDS = 60
KEPT = ("accepted", "corrected")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def enabled(store_dir, role: str = "worker") -> bool:
    if not store_path(store_dir, "decisions").exists():
        return False
    with closing(open_store(store_dir, "decisions", role)) as db:
        row = db.execute("SELECT value FROM settings WHERE target_id = ? AND key = ?", (TARGET, KEY)).fetchone()
    return bool(row and json.loads(row[0]) == "on")


def set_enabled(store_dir, on: bool, author: str = "owner") -> None:
    value = json.dumps("on" if on else "off")
    with closing(open_store(store_dir, "decisions", "web")) as db:
        db.execute("INSERT OR REPLACE INTO settings (target_id, key, value, author, at) VALUES (?,?,?,?,?)",
                   (TARGET, KEY, value, author, now()))
        db.execute("INSERT INTO decision_log (kind, target_id, detail, author, at) "
                   "VALUES ('life-evidence', ?, ?, ?, ?)", (TARGET, value, author, now()))
        db.commit()


def folder(store_dir) -> Path:
    return Path(store_dir) / FOLDER


def kept(store_dir, role: str = "web") -> dict[str, dict]:
    """Copies kept, by claim."""
    if not store_path(store_dir, "life").exists():
        return {}
    with closing(open_store(store_dir, "life", role)) as db:
        try:
            return {r["claim_id"]: dict(r) for r in db.execute("SELECT * FROM kept_evidence WHERE status = 'kept'")}
        except Exception:  # noqa: BLE001 - a store from before keeping evidence
            return {}


def copy(store_dir, claim_id: str, role: str = "web") -> bytes | None:
    """A kept copy's bytes, only from inside the evidence folder, and only while keeping is on and the event is still
    kept from a source the life story may use (the worker deletes the rest on its next pass)."""
    row = kept(store_dir, role).get(claim_id)
    if row is None or not row["path"] or not enabled(store_dir, role):
        return None
    if not any(c.claim_id == claim_id and c.state in KEPT for c in life.claims(store_dir, limit=10**6, role=role)):
        return None
    base = folder(store_dir).resolve()
    path = (base / row["path"]).resolve()
    if base not in path.parents or not path.is_file():
        return None
    return path.read_bytes()


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".keep-")
    with os.fdopen(fd, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _message(src, ref: str) -> dict | None:
    sid, _, native = ref.partition(":")
    if not sid or not native or "#" in native:
        return None
    row = src.execute("SELECT item_id, native_id, subject, from_addr, date_utc FROM items WHERE source_id = ? AND "
                      "native_id = ?", (sid, native)).fetchone()
    if row is None:
        return None
    part = src.execute("SELECT part_id, mime_type, charset FROM parts WHERE item_id = ? AND mime_type IN "
                       "('text/plain', 'text/html') AND (disposition IS NULL OR disposition != 'attachment') "
                       "ORDER BY mime_type != 'text/plain', part_id LIMIT 1", (row[0],)).fetchone()
    return {"source_id": sid, "item_id": row[0], "native_id": row[1], "subject": row[2], "from": row[3],
            "date": row[4], "part": dict(zip(("part_id", "mime_type", "charset"), part, strict=True)) if part else None}


def _delete(store_dir, db, claim_ids) -> int:
    base = folder(store_dir).resolve()
    n = 0
    for cid in claim_ids:
        row = db.execute("SELECT path FROM kept_evidence WHERE claim_id = ?", (cid,)).fetchone()
        if row and row[0]:
            path = (base / row[0]).resolve()
            if base in path.parents:
                path.unlink(missing_ok=True)
        db.execute("DELETE FROM kept_evidence WHERE claim_id = ?", (cid,))
        n += 1
    return n


def _later(attempts: int) -> str:
    """When to try a failed copy again: an hour, doubling up to a week."""
    return (datetime.now(timezone.utc) + timedelta(hours=min(2 ** attempts, 24 * 7))).isoformat(timespec="seconds")


RECHECK = timedelta(days=7)  # a message not indexed yet, or with no text yet, is looked at again


def keep_pending(config, clock=time.monotonic, slice_seconds: float = SLICE_SECONDS) -> list[dict]:
    """One worker pass: copy the evidence of newly kept events, and drop copies that are no longer wanted. Never
    raises: a problem here is recorded and the worker's other steps go on."""
    try:
        return _keep_pending(config, clock, slice_seconds)
    except Exception as exc:  # noqa: BLE001 - keeping copies is optional; it must never stop the worker
        return [{"step": "life-evidence", "stopped": type(exc).__name__}]


def _record(db, claim_id, ref, status, attempts=0, next_try=None, path=None, sha=None, size=None):
    db.execute("INSERT OR REPLACE INTO kept_evidence VALUES (?,?,?,?,?,?,?,?,?)",
               (claim_id, ref, path, sha, size, status, now(), attempts, next_try))


def _keep_pending(config, clock, slice_seconds) -> list[dict]:
    from towpath.adapters import build_connector
    from towpath.adapters.errors import NotFound, Unfetchable
    from towpath.life_propose import _stored_text, _text

    store_dir = config.store_dir
    if not store_path(store_dir, "life").exists():
        return []
    with closing(open_store(store_dir, "life", life.WRITER)) as db:
        have = {r[0]: dict(r) for r in db.execute("SELECT * FROM kept_evidence")}
        if not enabled(store_dir):
            stray = [p for p in folder(store_dir).glob("*.txt")] if folder(store_dir).is_dir() else []
            if not have and not stray:
                return []
            gone = _delete(store_dir, db, list(have))
            for p in stray:  # copies a crash left untracked
                p.unlink(missing_ok=True)
            db.commit()
            return [{"step": "life-evidence-deleted", "deleted": gone}]
        wanted = {c.claim_id: c for c in life.claims(store_dir, limit=10**6, role="worker") if c.state in KEPT}
        dropped = _delete(store_dir, db, [c for c in have if c not in wanted])  # unkept, or its source not allowed
        db.commit()
        stamp = now()

        def due(cid) -> bool:
            row = have.get(cid)
            if row is None:
                return True
            if row["status"] == "kept":  # a copy whose file went missing is made again
                return not (folder(store_dir) / (row["path"] or "")).is_file()
            if row["status"] in ("gone", "too-large"):
                return False
            return (row["next_try"] or "") <= stamp
        todo = sorted((c for cid, c in wanted.items() if c.citations and due(cid)),
                      key=lambda c: (c.claim_id in have, c.claim_id))  # never tried first
        if not todo or not store_path(store_dir, "source").exists():
            return [{"step": "life-evidence", "kept": 0, "dropped": dropped}] if dropped else []
        made, connectors, broken, deadline = 0, {}, set(), clock() + slice_seconds
        try:
            with closing(open_store(store_dir, "source", "worker")) as src:
                for claim in todo:
                    if clock() > deadline or not enabled(store_dir):  # turned off meanwhile: write nothing more
                        break
                    ref = claim.citations[0].ref
                    attempts = (have.get(claim.claim_id) or {}).get("attempts", 0) + 1
                    message = _message(src, ref)
                    if message is None or message["source_id"] not in getattr(config, "sources", {}):
                        _record(db, claim.claim_id, ref, "not-a-message", attempts,
                                (datetime.now(timezone.utc) + RECHECK).isoformat(timespec="seconds"))
                        continue
                    sid = message["source_id"]
                    if sid in broken:
                        continue
                    try:
                        text = _stored_text(config, message["item_id"])
                        if text is None and message["part"] is not None:
                            if sid not in connectors:
                                try:
                                    connectors[sid] = build_connector(config.sources[sid], config)
                                except Exception:
                                    broken.add(sid)  # not again this pass
                                    raise
                            text = _text(connectors[sid].fetch(message["native_id"], message["part"]["part_id"]),
                                         message["part"])
                    except Unfetchable:
                        _record(db, claim.claim_id, ref, "too-large", attempts)
                        continue
                    except NotFound:
                        _record(db, claim.claim_id, ref, "gone", attempts)  # deleted before it could be kept
                        continue
                    except Exception as exc:  # noqa: BLE001 - tried again later, less often each time
                        _record(db, claim.claim_id, ref, f"failed: {type(exc).__name__}", attempts, _later(attempts))
                        continue
                    if not text:
                        _record(db, claim.claim_id, ref, "no-text", attempts,
                                (datetime.now(timezone.utc) + RECHECK).isoformat(timespec="seconds"))
                        continue
                    data = (f"Event: {claim.statement} ({claim.when.expression})\nDate: {message['date'] or ''}\n"
                            f"From: {message['from'] or ''}\nSubject: {message['subject'] or ''}\n"
                            f"Reference: {ref}\n\n{text}\n").encode("utf-8")
                    rel = f"{claim.claim_id}.txt"
                    try:
                        _write(folder(store_dir) / rel, data)
                    except OSError as exc:  # a full or read-only disk: stop for this pass, try again later
                        _record(db, claim.claim_id, ref, f"failed: {type(exc).__name__}", attempts, _later(attempts))
                        break
                    _record(db, claim.claim_id, ref, "kept", attempts, None, rel, hashlib.sha256(data).hexdigest(),
                            len(data))
                    db.commit()
                    made += 1
        finally:
            db.commit()
            for c in connectors.values():
                c.close()
    return [{"step": "life-evidence", "kept": made, "dropped": dropped}] if made or dropped else []
