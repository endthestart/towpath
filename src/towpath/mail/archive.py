"""Archiving mail to a file, and removing it from the server only once each copy is verified.

The owner picks messages (on the cleanup pages) and asks for an archive, with or without removal from the server
(``maildecisions`` archive requests). The connector then, in bounded slices:

1. reads each whole message through the read-only connection (``BODY.PEEK[]``, so nothing is marked read);
2. appends it to ``archive/mail/<account>/<request>.mbox`` in the data folder (mboxrd: ``From `` lines escaped),
   flushed to disk;
3. reads it back from the file, undoes the escaping and checks the bytes' SHA-256, the size the server reported and
   the Message-ID against what Towpath read before; only then records it as archived (mail store ``archived``);
4. if the owner asked for removal, proposes moving it to Trash (``archive:<request>``). The action runner carries
   that out without a further question only for a message with a verified copy, and only when moving to Trash is
   turned on and the account isn't in dry runs only. Towpath never expunges; the provider empties its Trash.

Archived messages stay in Towpath's mail store, so they remain searchable after they leave the server.
"""

import hashlib
import json
import os
import re
import time
import uuid
from contextlib import closing
from datetime import datetime, timezone
from email.parser import BytesHeaderParser
from pathlib import Path

from towpath.canonical import digest
from towpath.mail import actions
from towpath.mail.text import MESSAGE_ID
from towpath.stores import open_store, store_path

ROLE = "connect"
SLICE_SECONDS = 90
BATCH = 25
MAX_ITEMS = 20_000  # per request
FROM_LINE = re.compile(rb"^>*From ")
ESCAPED = re.compile(rb"^>+From ")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def root(store_dir) -> Path:
    """The archive folder: archive/mail/ in the data folder (the stores' folder is the data folder's state/)."""
    return Path(store_dir).parent / "archive" / "mail"


# -- the owner's requests (web role) ------------------------------------------------------------------------------

def request(store_dir, source_id: str, item_ids: list[str], remove: bool, label: str, author: str = "owner") -> str:
    ids = list(dict.fromkeys(item_ids))
    if not ids:
        raise ValueError("choose some messages to archive")
    if len(ids) > MAX_ITEMS:
        raise ValueError(f"archive at most {MAX_ITEMS:,} messages at a time")
    rid = "arc_" + datetime.now(timezone.utc).strftime("%Y%m%d") + "_" + uuid.uuid4().hex[:8]
    with closing(open_store(store_dir, "maildecisions", "web")) as db:
        db.execute("INSERT INTO archive_requests VALUES (?,?,?,?,?,?,?)",
                   (rid, source_id, json.dumps(ids), int(remove), label[:120], author, now()))
        db.commit()
    return rid


def requests(store_dir, source_id: str | None = None, role: str = ROLE) -> list[dict]:
    if not store_path(store_dir, "maildecisions").exists():
        return []
    import sqlite3

    try:
        with closing(open_store(store_dir, "maildecisions", role)) as db:
            rows = [dict(r) for r in db.execute("SELECT * FROM archive_requests" +
                                                (" WHERE source_id = ?" if source_id else "") + " ORDER BY at DESC",
                                                (source_id,) if source_id else ())]
    except sqlite3.OperationalError:
        return []
    for r in rows:
        r["item_ids"] = json.loads(r["item_ids"])
    return rows


def progress(store_dir, request_id: str, role: str = "web") -> dict:
    out = {"archived": 0, "bytes": 0}
    if not store_path(store_dir, "mail").exists():
        return out
    with closing(open_store(store_dir, "mail", role)) as db:
        row = db.execute("SELECT count(*), coalesce(sum(length), 0) FROM archived WHERE request_id = ?",
                         (request_id,)).fetchone()
        state = db.execute("SELECT state FROM archive_progress WHERE request_id = ?", (request_id,)).fetchone()
    return {**(json.loads(state[0]) if state else {}), "archived": row[0], "bytes": row[1]}


# -- the file -----------------------------------------------------------------------------------------------------

def _lines(data: bytes) -> list[bytes]:
    """Lines ending at LF only (mbox readers split there; a lone CR is part of a line)."""
    parts = data.split(b"\n")
    return [p + b"\n" for p in parts[:-1]] + ([parts[-1]] if parts[-1] else [])


def escape(raw: bytes) -> bytes:
    return b"".join(b">" + line if FROM_LINE.match(line) else line for line in _lines(raw))


def unescape(data: bytes) -> bytes:
    return b"".join(line[1:] if ESCAPED.match(line) else line for line in _lines(data))


def _sync(f) -> None:
    """Flush to the disk itself (on macOS fsync alone leaves it in the drive's cache)."""
    f.flush()
    try:
        import fcntl

        if hasattr(fcntl, "F_FULLFSYNC"):
            fcntl.fcntl(f.fileno(), fcntl.F_FULLFSYNC)
            return
    except OSError:
        pass
    os.fsync(f.fileno())


def append(path: Path, raw: bytes) -> tuple[int, int]:
    """Append one message; returns (offset, length) of its escaped bytes, after its From line."""
    path.parent.mkdir(parents=True, exist_ok=True)
    body = escape(raw)
    stamp = datetime.now(timezone.utc).strftime("%a %b %d %H:%M:%S %Y").encode()
    with open(path, "ab") as f:
        f.write(b"From MAILER-DAEMON " + stamp + b"\n")
        offset = f.tell()
        f.write(body)
        f.write(b"\n" if body.endswith(b"\n") else b"\n\n")
        _sync(f)
    return offset, len(body)


def read_back(path: Path, offset: int, length: int) -> bytes:
    with open(path, "rb") as f:
        f.seek(offset)
        return unescape(f.read(length))


def verify(path: Path, offset: int, length: int, sha256: str, size: int | None, message_id: str | None) -> str | None:
    """Why the copy in the file doesn't match, or None when it does."""
    copy = read_back(path, offset, length)
    if hashlib.sha256(copy).hexdigest() != sha256:
        return "the copy's bytes differ"
    if size is not None and len(copy) != size:
        return "the copy's size differs from the server's"
    if message_id:
        found = MESSAGE_ID.findall(str(BytesHeaderParser().parsebytes(copy).get("Message-ID") or ""))
        if not found or found[0] != message_id:
            return "the copy's Message-ID differs"
    return None


def check_copy(store_dir, item_id: str, role: str = "act") -> str | None:
    """Why a message's archived copy can't be relied on now (re-read from its file), or None."""
    if not store_path(store_dir, "mail").exists():
        return "there is no record of an archive copy"
    with closing(open_store(store_dir, "mail", role)) as db:
        row = db.execute("SELECT path, offset, length, sha256, message_id FROM archived WHERE item_id = ?",
                         (item_id,)).fetchone()
    if row is None:
        return "there is no record of an archive copy"
    try:
        return verify(Path(row[0]), row[1], row[2], row[3], None, row[4])
    except OSError as exc:
        return f"the archive file couldn't be read ({type(exc).__name__})"


# -- the connector's slice --------------------------------------------------------------------------------------

def _propose_trash(config, request_id: str, source_id: str, record: dict, again: bool = False) -> None:
    reason = "Archived to a file and verified; you asked to remove it from the server."
    if again:  # a new change: the first was carried out as a dry run
        reason += " Proposed again now that dry runs are off."
    body = actions.make(source_id, "trash", actions.message_target(record), {}, reason=reason,
                        producer=f"archive:{request_id}")
    with closing(open_store(config.store_dir, "mailplan", "worker")) as plan:
        plan.execute("INSERT OR IGNORE INTO proposals (proposal_id, source_id, body, digest, created_at) "
                     "VALUES (?,?,?,?,?)", (actions.proposal_id(body), source_id, json.dumps(body, sort_keys=True),
                                            digest(body), now()))
        plan.commit()


def archive_pending(config, slice_seconds: float = SLICE_SECONDS, clock=time.monotonic) -> list[dict]:
    from towpath.adapters import build_connector
    from towpath.adapters.errors import SyncStop
    from towpath.adapters.imap import _mapped_errors
    from towpath.mail import analysis

    eligible = set(analysis.eligible(config))
    summaries = []
    deadline = clock() + slice_seconds
    for req in reversed(requests(config.store_dir)):
        sid = req["source_id"]
        if sid not in eligible or clock() >= deadline:
            continue
        mail = open_store(config.store_dir, "mail", ROLE)
        connector = None
        try:
            state = json.loads((mail.execute("SELECT state FROM archive_progress WHERE request_id = ?",
                                             (req["request_id"],)).fetchone() or ["{}"])[0])
            if req["remove"]:
                _repropose_after_dry_runs(config, mail, req)
            # Archived already, by this request or an earlier one: one copy is enough; the removal still applies.
            done = set()
            for start in range(0, len(req["item_ids"]), 900):
                chunk = req["item_ids"][start:start + 900]
                done |= {r[0] for r in mail.execute(
                    f"SELECT item_id FROM archived WHERE item_id IN ({','.join('?' for _ in chunk)})", chunk)}
            earlier = sorted(done - set(state.get("counted", [])))
            if earlier and req["remove"]:
                for r in mail.execute(f"SELECT item_id, mailbox, uidvalidity, uid, message_id FROM messages WHERE "
                                      f"item_id IN ({','.join('?' for _ in earlier[:900])})", earlier[:900]):
                    if check_copy(config.store_dir, r["item_id"], role=ROLE) is None:
                        _propose_trash(config, req["request_id"], sid, dict(r))
            state["counted"] = sorted(set(state.get("counted", [])) | set(earlier[:900]))
            todo = [i for i in req["item_ids"] if i not in done]
            skipped = set(state.get("skipped", []))
            todo = [i for i in todo if i not in skipped]
            if not todo:
                mail.execute("INSERT OR REPLACE INTO archive_progress VALUES (?,?,?)",
                             (req["request_id"], json.dumps(state), now()))
                mail.commit()
                continue
            path = root(config.store_dir) / sid / f"{req['request_id']}.mbox"
            archived = 0
            with _mapped_errors():
                connector = build_connector(config.sources[sid], config)
                session = connector.session
                for start in range(0, len(todo), BATCH):
                    if clock() >= deadline:
                        break
                    marks = ",".join("?" for _ in todo[start:start + BATCH])
                    records = [dict(r) for r in mail.execute(
                        f"SELECT item_id, mailbox, uidvalidity, uid, message_id, size FROM messages WHERE item_id IN "
                        f"({marks})", todo[start:start + BATCH])]
                    known = {r["item_id"] for r in records}
                    skipped.update(i for i in todo[start:start + BATCH] if i not in known)
                    by_box: dict[tuple, list[dict]] = {}
                    for r in records:
                        by_box.setdefault((r["mailbox"], r["uidvalidity"]), []).append(r)
                    for (mailbox, uidvalidity), group in by_box.items():
                        if mailbox not in connector._mailboxes() or \
                                session.examine(mailbox)["uidvalidity"] != uidvalidity:
                            skipped.update(r["item_id"] for r in group)
                            continue
                        got = session.fetch([r["uid"] for r in group], ("RFC822.SIZE", "BODY.PEEK[]"))
                        for r in group:
                            data = got.get(r["uid"])
                            raw = data.get(b"BODY[]") if data else None
                            if raw is None:
                                skipped.add(r["item_id"])  # gone from the server since Towpath read it
                                continue
                            sha = hashlib.sha256(raw).hexdigest()
                            offset, length = append(path, raw)
                            problem = verify(path, offset, length, sha, data.get(b"RFC822.SIZE"), r["message_id"])
                            if problem:
                                state.setdefault("problems", []).append({"item_id": r["item_id"], "problem": problem})
                                skipped.add(r["item_id"])
                                continue
                            mail.execute("INSERT OR IGNORE INTO archived VALUES (?,?,?,?,?,?,?,?,?)",
                                         (r["item_id"], sid, req["request_id"], str(path), offset, length, sha,
                                          r["message_id"], now()))
                            mail.commit()
                            state["counted"] = sorted(set(state.get("counted", [])) | {r["item_id"]})
                            archived += 1
                            if req["remove"]:
                                _propose_trash(config, req["request_id"], sid, r)
            state = {**state, "skipped": sorted(skipped), "file": str(path)}
            mail.execute("INSERT OR REPLACE INTO archive_progress VALUES (?,?,?)",
                         (req["request_id"], json.dumps(state), now()))
            mail.commit()
            if archived:
                summaries.append({"source_id": sid, "step": "mail-archive", "request": req["request_id"],
                                  "archived": archived})
        except SyncStop as exc:
            summaries.append({"source_id": sid, "step": "mail-archive", "stopped": exc.reason})
        finally:
            mail.close()
            if connector is not None:
                connector.close()
    return summaries


def auto_approved(store_dir, body: dict, role: str = "act", cache: dict | None = None) -> bool:
    """A Trash proposal from an archive request the owner made with removal, for a message whose copy is verified."""
    producer = str(body.get("producer", ""))
    if body.get("type") != "trash" or not producer.startswith("archive:"):
        return False
    rid = producer[len("archive:"):]
    if cache is not None and "archive_requests" in cache:
        listed = cache["archive_requests"]
    else:
        listed = {r["request_id"]: r for r in requests(store_dir, role=role)}
        if cache is not None:
            cache["archive_requests"] = listed
    req = listed.get(rid)
    item_id = body["target"].get("item_id")
    if not req or not req["remove"] or item_id not in req["item_ids"] or not store_path(store_dir, "mail").exists():
        return False
    with closing(open_store(store_dir, "mail", role)) as db:
        copy = db.execute("SELECT message_id FROM archived WHERE item_id = ?", (item_id,)).fetchone()
        message = db.execute("SELECT * FROM messages WHERE item_id = ?", (item_id,)).fetchone()
    # The change must aim at exactly the message that was copied: the same mailbox, number and Message-ID. The copy
    # itself is read back again just before the change is carried out.
    return copy is not None and message is not None and copy[0] == message["message_id"] and \
        actions.message_target(dict(message)) == body["target"]


def _repropose_after_dry_runs(config, mail, req: dict) -> None:
    """Removals that ran as dry runs are proposed again once the account is live and Trash is turned on."""
    from towpath.mail import settings

    sid = req["source_id"]
    if settings.dry_run(config.store_dir, sid, role=ROLE) or \
            "trash" not in settings.enabled_types(config.store_dir, sid, role=ROLE) or \
            not store_path(config.store_dir, "mailact").exists():
        return
    producer = f"archive:{req['request_id']}"
    with closing(open_store(config.store_dir, "mailact", ROLE)) as act:
        tried = [json.loads(r[0]) for r in act.execute("SELECT body FROM receipts WHERE outcome = 'dry-run' AND "
                                                         "body LIKE ?", (f'%"{producer}"%',))]
    for body in tried:
        if body.get("producer") != producer or body.get("type") != "trash" or "again" in body.get("reason", ""):
            continue
        row = mail.execute("SELECT item_id, mailbox, uidvalidity, uid, message_id FROM messages WHERE item_id = ?",
                           (body["target"].get("item_id"),)).fetchone()
        if row is not None:
            _propose_trash(config, req["request_id"], sid, dict(row), again=True)
