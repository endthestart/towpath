"""The connector's slice: copying mail, verifying it, and only then proposing it for the trash."""

import hashlib
import json
import time
from contextlib import closing

from towpath.canonical import digest
from towpath.mail import actions
from towpath.mail.archive.common import BATCH, ROLE, SLICE_SECONDS, now, root
from towpath.mail.archive.copies import append, check_copy, verify
from towpath.mail.archive.owner_requests import requests
from towpath.stores import open_store, store_path


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
