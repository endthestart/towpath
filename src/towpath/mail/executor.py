"""The action runner for mailbox changes (role ``act``): the only code that holds an account's write password.

Inputs are changes the owner asked for directly (``maildecisions`` requests) and proposals the owner approved
(``mailplan`` proposals with a matching answer in ``maildecisions``). It ignores classifications,
model output and anything else. Before acting it checks the digest, the type, the owner's per-type switch and the
message itself (identity, and Message-ID where known). While the account's "dry runs only" switch is on, or for
an action type the owner hasn't turned on, or an account without a write password, a change runs as a **dry
run**: the message is checked through the read-only connection and the receipt says what would have changed.
Every request gets exactly one receipt; a receipt is never replaced.

:class:`ActSession` is the whole write surface: SELECT, UID STORE of ``\\Seen``, ``\\Flagged`` and labels (never
``\\Deleted``), UID MOVE to an existing mailbox, APPEND to Drafts or the inbox, and UNSELECT. There is no EXPUNGE,
CLOSE, DELETE, COPY, CREATE or RENAME, and the IMAP client is never handed out.
"""

import json
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from towpath.canonical import digest
from towpath.mail import actions, settings
from towpath.mail.actions import APPEND_TYPES, FLAG_TYPES, ActionError
from towpath.stores import open_store, store_path

ROLE = "act"
LIMIT = 50  # changes per account per poll
PAUSE_SECONDS = 0.2  # between changes
EXPIRES_DAYS = 14  # a proposal approved later than this after it was made is not carried out
READ_ITEMS = ("FLAGS", "BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)]")
SYSTEM_FLAGS = {"\\Seen", "\\Flagged"}
APPEND_FLAGS = {"\\Seen", "\\Draft"}


class WriteViolation(RuntimeError):
    """Towpath was about to send a command outside the action runner's allow-list."""


class TargetChanged(Exception):
    """The message isn't where the change expects it (moved, deleted, or a different message)."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def password_path(credentials_dir: Path, source_id: str) -> Path:
    from towpath.connections import write_secret_path

    return write_secret_path(credentials_dir, source_id)


def _text(value) -> str:
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)


def _message_id(data: dict) -> str | None:
    from towpath.mail.text import MESSAGE_ID

    raw = next((v for k, v in data.items() if isinstance(k, bytes) and k.startswith(b"BODY[HEADER.FIELDS")), b"")
    found = MESSAGE_ID.findall(_text(raw or b""))
    return found[0] if found else None


class ActSession:
    """The only IMAP commands the action runner sends."""

    def __init__(self, client):
        self._client = client
        self._caps: set[str] | None = None
        self._folders: list[tuple[str, list[str]]] | None = None
        self._selected: str | None = None

    def login(self, username: str, password: str) -> None:
        self._client.login(username, password)

    def capabilities(self) -> set[str]:
        if self._caps is None:
            self._caps = {_text(c).upper() for c in self._client.capabilities()}
        return self._caps

    def folders(self) -> list[tuple[str, list[str]]]:
        if self._folders is None:
            self._folders = [(_text(name), [_text(f) for f in flags])
                             for flags, _, name in self._client.list_folders()
                             if not any(_text(f).lower() in {"\\noselect", "\\nonexistent"} for f in flags)]
        return self._folders

    def special(self, use: str) -> str | None:
        """The mailbox with an RFC 6154 special use (``\\Archive``, ``\\Trash``, ``\\Drafts``), if there is one."""
        return next((name for name, flags in self.folders() if use.lower() in {f.lower() for f in flags}), None)

    def select(self, mailbox: str) -> int:
        if mailbox not in {n for n, _ in self.folders()}:
            raise TargetChanged(f"there is no mailbox named {mailbox}")
        info = self._client.select_folder(mailbox, readonly=False)
        if b"READ-WRITE" not in info:
            raise WriteViolation("the server opened the mailbox read-only; is this the write password?")
        self._selected = mailbox
        return int(info[b"UIDVALIDITY"])

    def read(self, uid: int) -> dict | None:
        rows = self._client.fetch([uid], list(READ_ITEMS))
        if uid not in rows:
            return None
        return {"flags": sorted(_text(f) for f in rows[uid].get(b"FLAGS", ())), "message_id": _message_id(rows[uid])}

    def find(self, message_id: str) -> list[int]:
        return sorted(int(u) for u in self._client.search(["HEADER", "Message-ID", message_id]))

    def store(self, uid: int, sign: str, flag: str) -> None:
        if flag not in SYSTEM_FLAGS and not actions.KEYWORD.match(flag):
            raise WriteViolation(f"STORE of {flag!r} is not allowed")
        (self._client.add_flags if sign == "+" else self._client.remove_flags)([uid], [flag])

    def move(self, uid: int, mailbox: str) -> None:
        if "MOVE" not in self.capabilities():
            raise WriteViolation("the server lacks MOVE; Towpath doesn't copy and expunge instead")
        if mailbox not in {n for n, _ in self.folders()} or mailbox == self._selected:
            raise TargetChanged(f"can't move to {mailbox}")
        self._client.move([uid], mailbox)

    def append(self, role: str, raw: str, flags: tuple[str, ...]) -> str:
        mailbox = "INBOX" if role == "inbox" else self.special("\\Drafts")
        if mailbox is None:
            raise TargetChanged("the account has no Drafts mailbox")
        if set(flags) - APPEND_FLAGS:
            raise WriteViolation(f"APPEND with flags {sorted(flags)} is not allowed")
        self._client.append(mailbox, raw.encode("utf-8"), flags=flags)
        return mailbox

    def unselect(self) -> None:
        """Leave the mailbox without expunging: UNSELECT, or EXAMINE it (never CLOSE, which expunges)."""
        if self._selected is None:
            return
        if "UNSELECT" in self.capabilities():
            self._client.unselect_folder()
        else:
            self._client.select_folder(self._selected, readonly=True)
        self._selected = None

    def logout(self) -> None:
        try:
            self._client.logout()
        except Exception:  # noqa: BLE001 - a failed goodbye changes nothing
            pass


def connect_act(source, password: str) -> ActSession:
    from imapclient import IMAPClient

    from towpath.adapters.imap import LOOPBACK
    from towpath.adapters.errors import RequestStop

    if source.security == "plain-loopback" and source.host not in LOOPBACK:
        raise RequestStop("plain IMAP is allowed only to a loopback address")
    client = IMAPClient(source.host, port=source.port, ssl=source.security == "tls", timeout=source.timeout_seconds)
    try:
        if source.security == "starttls":
            client.starttls()
        session = ActSession(client)
        session.login(source.username, password)
    except BaseException:
        try:
            client.shutdown()
        except Exception:  # noqa: BLE001
            pass
        raise
    return session


# -- what is waiting ------------------------------------------------------------------------------------------

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
    from towpath.mail import rules

    answers = settings.answers(store_dir, role=role)
    with closing(open_store(store_dir, "mailplan", role)) as p:
        for r in p.execute("SELECT * FROM proposals ORDER BY created_at"):
            answer = answers.get(r["proposal_id"])
            if r["proposal_id"] in done or (answer and answer["decision"] != "approved"):
                continue
            body = json.loads(r["body"])
            if answer is None:
                if not rules.auto_approved(store_dir, body, role=role):
                    continue
                answer = {"digest": r["digest"]}  # the owner set the rule that proposed it to run by itself
            out.append({"key": r["proposal_id"], "source_id": r["source_id"], "body": body, "digest": r["digest"],
                        "created_at": r["created_at"], "approved_digest": answer["digest"],
                        "approved": answer["digest"] == r["digest"]})
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
    if isinstance(target.get("uid"), int):
        found = read_uid(target["uid"])
        if found is None:
            raise TargetChanged("the message is no longer in that mailbox")
        if target.get("message_id") and found["message_id"] and found["message_id"] != target["message_id"]:
            raise TargetChanged("a different message now has that number")
        return target["uid"]
    uids = find(target["message_id"])
    if len(uids) != 1:
        raise TargetChanged("the message wasn't found" if not uids else "more than one message has that Message-ID")
    return uids[0]


def _dry_run(connector, body: dict) -> tuple[str, dict]:
    """Check the target through the read-only connection; say what would change."""
    what = actions.describe(body)
    if body["type"] in APPEND_TYPES:
        where = "the inbox" if APPEND_TYPES[body["type"]] == "inbox" else "Drafts"
        return f"Dry run: would add a message to {where}.", {}
    session, target = connector.session, body["target"]
    if target["mailbox"] not in connector._mailboxes():
        raise TargetChanged(f"there is no mailbox named {target['mailbox']}")
    info = session.examine(target["mailbox"])
    if isinstance(target.get("uidvalidity"), int) and info["uidvalidity"] != target["uidvalidity"]:
        raise TargetChanged("the mailbox was rebuilt since this message was read")

    def read_uid(uid):
        rows = session.fetch([uid], READ_ITEMS)
        return {"flags": sorted(_text(f) for f in rows[uid].get(b"FLAGS", ())),
                "message_id": _message_id(rows[uid])} if uid in rows else None

    uid = _locate(read_uid, session.search_message_id, target)
    before = {**read_uid(uid), "mailbox": target["mailbox"], "uid": uid}
    return f"Dry run: would {what[0].lower()}{what[1:]}; the message was checked and left unchanged.", before


def _execute(act: ActSession, body: dict) -> tuple[str, dict, dict]:
    kind, target, params = body["type"], body["target"], body["params"]
    if kind in APPEND_TYPES:
        flags = ("\\Draft", "\\Seen") if kind == "draft" else ()
        mailbox = act.append(APPEND_TYPES[kind], params["message"], flags)
        return f"Added to {mailbox}.", {}, {"mailbox": mailbox, "message_id": actions.appended_message_id(body)}
    uidvalidity = act.select(target["mailbox"])
    try:
        if isinstance(target.get("uidvalidity"), int) and uidvalidity != target["uidvalidity"]:
            raise TargetChanged("the mailbox was rebuilt since this message was read")
        uid = _locate(act.read, act.find, target)
        before = {**act.read(uid), "mailbox": target["mailbox"], "uid": uid}
        if kind in FLAG_TYPES:
            sign, flag = FLAG_TYPES[kind]
            act.store(uid, sign, flag)
            after = {**act.read(uid), "mailbox": target["mailbox"], "uid": uid}
        elif kind in ("add_keyword", "remove_keyword"):
            act.store(uid, "+" if kind == "add_keyword" else "-", params["keyword"])
            after = {**act.read(uid), "mailbox": target["mailbox"], "uid": uid}
        else:
            dest = {"archive": lambda: act.special("\\Archive"), "trash": lambda: act.special("\\Trash"),
                    "move": lambda: params["mailbox"]}[kind]()
            if dest is None:
                raise TargetChanged(f"the account has no {'Archive' if kind == 'archive' else 'Trash'} mailbox")
            act.move(uid, dest)
            after = {"mailbox": dest, "message_id": before["message_id"]}
    finally:
        act.unselect()
    return f"Done: {actions.describe(body)}.", before, after


def run_pending(config, credentials_dir, limit: int = LIMIT, pause: float = PAUSE_SECONDS) -> list[dict]:
    """Carry out what is waiting, account by account. Never raises for a mailbox problem."""
    from towpath.adapters import build_connector
    from towpath.adapters.errors import SyncStop
    from towpath.adapters.imap import _mapped_errors

    managed = settings.managed(config.store_dir, role=ROLE)
    by_source: dict[str, list[dict]] = {}
    for item in pending(config.store_dir):
        if item["source_id"] in managed and item["source_id"] in config.sources and \
                config.sources[item["source_id"]].adapter == "imap":
            by_source.setdefault(item["source_id"], []).append(item)
    summaries = []
    db = open_store(config.store_dir, "mailact", ROLE)
    try:
        for sid, items in by_source.items():
            source = config.sources[sid]
            dry = settings.dry_run(config.store_dir, sid)
            enabled = set() if dry else settings.enabled_types(config.store_dir, sid)
            secret = password_path(credentials_dir, sid)
            reader, act, counts = None, None, {}
            try:
                for item in items[:limit]:
                    problem = _check(item)
                    if problem:
                        _receipt(db, item, "refused", problem)
                        counts["refused"] = counts.get("refused", 0) + 1
                        continue
                    body = item["body"]
                    live = body["type"] in enabled and secret.is_file()
                    try:
                        with _mapped_errors():
                            if live:
                                if act is None:
                                    act = connect_act(source, secret.read_text().strip())
                                detail, before, after = _execute(act, body)
                                inverse = actions.inverse(body, before, after)
                                _receipt(db, item, "done", detail, before, after, inverse)
                                counts["done"] = counts.get("done", 0) + 1
                            else:
                                if reader is None:
                                    reader = build_connector(source, config)
                                detail, before = _dry_run(reader, body)
                                _receipt(db, item, "dry-run", detail, before)
                                counts["dry-run"] = counts.get("dry-run", 0) + 1
                    except TargetChanged as exc:
                        _receipt(db, item, "skipped", f"Not done: {exc}.")
                        counts["skipped"] = counts.get("skipped", 0) + 1
                    except SyncStop:
                        raise  # the account or network stopped: this change waits for the next poll
                    except Exception as exc:  # noqa: BLE001 - recorded once, then the account's batch stops
                        reason = str(exc) if isinstance(exc, WriteViolation) else type(exc).__name__
                        _receipt(db, item, "failed",
                                 f"Stopped on an error ({reason}). Check the message in your mail app.")
                        raise
                    if pause:
                        time.sleep(pause)
            except SyncStop as exc:
                summaries.append({"source_id": sid, "step": "mail-actions", "stopped": exc.reason, **counts})
                continue
            except Exception as exc:  # noqa: BLE001 - stop this account's batch; keep data out of the record
                summaries.append({"source_id": sid, "step": "mail-actions",
                                  "stopped": f"unexpected {type(exc).__name__}", **counts})
                continue
            finally:
                if act is not None:
                    act.logout()
                if reader is not None:
                    reader.close()
            summaries.append({"source_id": sid, "step": "mail-actions", **counts})
    finally:
        db.close()
    return summaries
