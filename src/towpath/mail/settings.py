"""The owner's mail-management choices, in the decisions store (written by the web role, logged).

- Which accounts are managed: the ``mail-manage`` source grant (towpath.unified.grants).
- Whether reading an account's mail is paused.
- Dry runs only: an account-wide switch, on until the owner turns it off. While it is on, nothing changes mail.
- Which action types may change mail once dry runs are off. A type not listed still runs as a dry run: the action
  runner checks the message and records what it would have done, and changes nothing. All types start off.
Answers to proposed changes, tied to the digest of what was proposed, and changes the owner asks for directly (a
button on a message, Undo) live in their own store, ``maildecisions``, also written only by the web role.
"""

import json
from contextlib import closing
from datetime import datetime, timezone

from towpath.canonical import digest
from towpath.mail import FEATURE
from towpath.stores import open_store, store_path

WRITER = "web"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def target(source_id: str) -> str:
    return f"mail:{source_id}"


def managed(store_dir, role: str = "connect") -> set[str]:
    """Accounts the owner turned mail management on for."""
    if not store_path(store_dir, "decisions").exists():
        return set()
    with closing(open_store(store_dir, "decisions", role)) as db:
        return {r[0] for r in db.execute("SELECT source_id FROM source_grants WHERE feature = ?", (FEATURE,))}


def _get(store_dir, source_id: str, key: str, role: str):
    if not store_path(store_dir, "decisions").exists():
        return None
    with closing(open_store(store_dir, "decisions", role)) as db:
        row = db.execute("SELECT value FROM settings WHERE target_id = ? AND key = ?",
                         (target(source_id), key)).fetchone()
    return json.loads(row[0]) if row else None


def _set(store_dir, source_id: str, key: str, value, kind: str, author: str) -> None:
    with closing(open_store(store_dir, "decisions", WRITER)) as db:
        db.execute("INSERT OR REPLACE INTO settings (target_id, key, value, author, at) VALUES (?,?,?,?,?)",
                   (target(source_id), key, json.dumps(value, sort_keys=True), author, now()))
        db.execute("INSERT INTO decision_log (kind, target_id, detail, author, at) VALUES (?,?,?,?,?)",
                   (kind, target(source_id), json.dumps({key: value}, sort_keys=True), author, now()))
        db.commit()


def paused(store_dir, source_id: str, role: str = "connect") -> bool:
    return _get(store_dir, source_id, "analysis", role) == "paused"


def set_paused(store_dir, source_id: str, value: bool, author: str = "owner") -> None:
    _set(store_dir, source_id, "analysis", "paused" if value else "running", "mail-analysis", author)


def dry_run(store_dir, source_id: str, role: str = "act") -> bool:
    """Whether every change to this account is a dry run. On unless the owner turned it off."""
    return _get(store_dir, source_id, "dry_run", role) is not False


def set_dry_run(store_dir, source_id: str, value: bool, author: str = "owner") -> None:
    _set(store_dir, source_id, "dry_run", bool(value), "mail-dry-run", author)


def enabled_types(store_dir, source_id: str, role: str = "act") -> set[str]:
    """Action types allowed to change this account's mail. Everything else is a dry run."""
    return set(_get(store_dir, source_id, "writes", role) or ())


def set_enabled_types(store_dir, source_id: str, types: set[str], author: str = "owner") -> None:
    from towpath.mail.actions import TYPES

    unknown = set(types) - set(TYPES)
    if unknown:
        raise ValueError(f"unknown action types: {', '.join(sorted(unknown))}")
    _set(store_dir, source_id, "writes", sorted(types), "mail-writes", author)


# -- answers to proposals, and direct requests ------------------------------------------------------------------

def answer(store_dir, proposal_id: str, proposal_digest: str, decision: str, author: str = "owner") -> None:
    if decision not in ("approved", "rejected"):
        raise ValueError("decision must be approved or rejected")
    with closing(open_store(store_dir, "maildecisions", WRITER)) as db:
        db.execute("INSERT OR REPLACE INTO answers (proposal_id, digest, decision, author, at) VALUES (?,?,?,?,?)",
                   (proposal_id, proposal_digest, decision, author, now()))
        db.commit()


def answers(store_dir, role: str = "act") -> dict[str, dict]:
    with closing(open_store(store_dir, "maildecisions", role)) as db:
        return {r["proposal_id"]: dict(r) for r in db.execute("SELECT * FROM answers")}


def ask_many(store_dir, source_id: str, bodies: list[dict], author: str = "owner") -> int:
    """Append many changes the owner asked for at once (a clean-up). Returns how many."""
    from towpath.mail.actions import validate

    for body in bodies:
        validate(body)
        if body["source_id"] != source_id:
            raise ValueError("a change names a different account")
    with closing(open_store(store_dir, "maildecisions", WRITER)) as db:
        db.executemany("INSERT INTO requests (source_id, body, digest, author, created_at) VALUES (?,?,?,?,?)",
                       [(source_id, json.dumps(b, sort_keys=True), digest(b), author, now()) for b in bodies])
        db.commit()
    return len(bodies)


def ask(store_dir, source_id: str, body: dict, author: str = "owner") -> int:
    """Append a change the owner asked for (approved by being asked for). Returns its sequence number."""
    from towpath.mail.actions import validate

    validate(body)
    if body["source_id"] != source_id:
        raise ValueError("the change names a different account")
    with closing(open_store(store_dir, "maildecisions", WRITER)) as db:
        cur = db.execute("INSERT INTO requests (source_id, body, digest, author, created_at) VALUES (?,?,?,?,?)",
                         (source_id, json.dumps(body, sort_keys=True), digest(body), author, now()))
        db.commit()
        return cur.lastrowid
