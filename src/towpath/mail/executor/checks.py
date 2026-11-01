"""Which changes are waiting, what has been done, and whether a change may still be carried out."""

import json
from contextlib import closing
from datetime import datetime, timedelta, timezone

from towpath.canonical import digest
from towpath.mail import actions, settings
from towpath.mail.actions import ActionError
from towpath.mail.executor.session import EXPIRES_DAYS, ROLE, TargetChanged, now
from towpath.stores import open_store, store_path


def pending(store_dir, role: str = ROLE) -> list[dict]:
    """Requests and approved proposals without a receipt, oldest first."""
    done = set()
    if store_path(store_dir, "mailact").exists() or role == ROLE:
        with closing(open_store(store_dir, "mailact", role)) as db:
            done = {r[0] for r in db.execute("SELECT key FROM receipts")}
    out = []
    with closing(open_store(store_dir, "maildecisions", role)) as q:
        for r in q.execute("SELECT * FROM requests ORDER BY seq").fetchall():
            key = f"req_{r['seq']:06d}"
            if key not in done:
                out.append({"key": key, "source_id": r["source_id"], "body": json.loads(r["body"]),
                            "digest": r["digest"], "created_at": r["created_at"], "approved": True})
    from towpath.mail import archive, notify, rules

    answers = settings.answers(store_dir, role=role)
    cache: dict = {}  # rules, archive requests and settings, read once for every proposal below
    with closing(open_store(store_dir, "mailplan", role)) as p:
        for r in p.execute("SELECT * FROM proposals ORDER BY created_at"):
            answer = answers.get(r["proposal_id"])
            if r["proposal_id"] in done or (answer and answer["decision"] != "approved"):
                continue
            body = json.loads(r["body"])
            if answer is None:
                if not (rules.auto_approved(store_dir, body, role=role, cache=cache) or
                        archive.auto_approved(store_dir, body, role=role, cache=cache) or
                        notify.auto_approved(store_dir, body, role=role, cache=cache)):
                    continue
                answer = {"digest": r["digest"], "auto": True}  # the owner's own rule or archive request covers it
            out.append({"key": r["proposal_id"], "source_id": r["source_id"], "body": body, "digest": r["digest"],
                        "created_at": r["created_at"], "approved_digest": answer["digest"],
                        "approved": answer["digest"] == r["digest"], "auto": bool(answer.get("auto"))})
    return out


def receipts(store_dir, source_id: str | None = None, limit: int = 100, role: str = "web") -> list[dict]:
    if not store_path(store_dir, "mailact").exists():
        return []
    with closing(open_store(store_dir, "mailact", role)) as db:
        rows = db.execute("SELECT * FROM receipts" + (" WHERE source_id = ?" if source_id else "") +
                          " ORDER BY at DESC, key DESC LIMIT ?", ((source_id, limit) if source_id else (limit,)))
        out = []
        for r in rows:
            d = dict(r)
            for k in ("body", "before", "after", "inverse"):
                d[k] = json.loads(d[k]) if d[k] else None
            out.append(d)
    return out


# -- carrying changes out -------------------------------------------------------------------------------------

def _receipt(db, item: dict, outcome: str, detail: str, before=None, after=None, inverse=None) -> dict:
    row = {"key": item["key"], "source_id": item["source_id"], "outcome": outcome, "detail": detail}
    db.execute("INSERT OR IGNORE INTO receipts (key, source_id, body, digest, outcome, detail, before, after, inverse, "
               "at) VALUES (?,?,?,?,?,?,?,?,?,?)",
               (item["key"], item["source_id"], json.dumps(item["body"], sort_keys=True), item["digest"], outcome,
                detail, json.dumps(before) if before else None, json.dumps(after) if after else None,
                json.dumps(inverse, sort_keys=True) if inverse else None, now()))
    db.commit()
    return row


def _check(item: dict) -> str | None:
    """Why the request can't be carried out at all, or None."""
    if digest(item["body"]) != item["digest"]:
        return "The change doesn't match what was recorded; nothing was done."
    if not item["approved"]:
        return "The approval was for a different version of this change; nothing was done."
    try:
        actions.validate(item["body"])
    except ActionError as exc:
        return f"Not a valid change ({exc}); nothing was done."
    if item["body"]["source_id"] != item["source_id"]:
        return "The change names a different account; nothing was done."
    created = datetime.fromisoformat(item["created_at"])
    if item["key"].startswith("mact_") and datetime.now(timezone.utc) - created > timedelta(days=EXPIRES_DAYS):
        return "Approved too long after it was proposed; propose it again."
    return None


def _locate(read_uid, find, target: dict) -> int:
    """The UID the change applies to in the selected or examined mailbox."""
    if isinstance(target.get("uid"), int) and isinstance(target.get("uidvalidity"), int):
        found = read_uid(target["uid"])
        if found is None:
            raise TargetChanged("the message is no longer in that mailbox")
        if target.get("message_id") and found["message_id"] != target["message_id"]:
            raise TargetChanged("a different message now has that number")
        return target["uid"]
    if not target.get("message_id"):
        raise TargetChanged("the message can't be identified")
    uids = find(target["message_id"])
    if len(uids) != 1:
        raise TargetChanged("the message wasn't found" if not uids else "more than one message has that Message-ID")
    found = read_uid(uids[0])
    if found is None or found["message_id"] != target["message_id"]:
        raise TargetChanged("the message wasn't found")
    return uids[0]
