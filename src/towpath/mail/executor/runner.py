"""Carrying out changes: as a dry run until the owner turns the type on, with a receipt for each."""

import time
from pathlib import Path

from towpath.mail import actions, settings
from towpath.mail.actions import APPEND_TYPES, FLAG_TYPES
from towpath.mail.executor.checks import _check, _locate, pending, _receipt
from towpath.mail.executor.session import (
    LIMIT,
    PAUSE_SECONDS,
    READ_ITEMS,
    ROLE,
    ActSession,
    TargetChanged,
    WriteViolation,
    connect_act,
    _message_id,
    password_path,
    _text,
)
from towpath.stores import open_store


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


def _execute(act: ActSession, body: dict, trash_ok: bool = True) -> tuple[str, dict, dict]:
    """Carry one change out. ``trash_ok``: whether a move into Trash may happen (the owner turned on "Move to Trash"
    and approved this change themselves); a move is a Trash in all but name when it goes there."""
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
            dest = {"archive": lambda: act.special("\\Archive") or act.special("\\All"),  # Gmail: All Mail
                    "trash": lambda: act.special("\\Trash"), "move": lambda: params["mailbox"]}[kind]()
            if dest is None:
                raise TargetChanged(f"the account has no {'Archive' if kind == 'archive' else 'Trash'} mailbox")
            if kind == "move" and not trash_ok and (dest == act.special("\\Trash") or
                                                    dest.strip().lower() in actions.TRASH_NAMES):
                raise TargetChanged("moving to Trash needs “Move to Trash” turned on, and your own approval")
            act.move(uid, dest)
            after = {"mailbox": dest, "message_id": before["message_id"]}
    finally:
        act.unselect()
    return f"Done: {actions.describe(body)}.", before, after


def _unsubscribe(db, item: dict, source, secret: Path, enabled: bool) -> str:
    """Carry out (or dry-run) one unsubscribe; recorded either way, never raised."""
    from urllib.parse import urlsplit

    from towpath.mail import unsubscribe

    params = item["body"]["params"]
    where = urlsplit(params["url"]).hostname if params["method"] == "post" else params["url"][len("mailto:"):]
    if not enabled or (params["method"] == "mail" and not secret.is_file()):
        what = f"send a one-click unsubscribe request to {where}" if params["method"] == "post" else \
            f"email {where.split('?')[0]} to unsubscribe"
        _receipt(db, item, "dry-run", f"Dry run: would {what}.")
        return "dry-run"
    try:
        detail = (unsubscribe.one_click(params["url"]) if params["method"] == "post" else
                  unsubscribe.send_mail(source, secret.read_text().strip(), params["url"],
                                        list_from=item["body"]["target"]["sender"], list_id=params.get("list_id")))
    except unsubscribe.UnsubscribeError as exc:
        _receipt(db, item, "failed", f"Not unsubscribed: {exc}.")
        return "failed"
    except Exception as exc:  # noqa: BLE001 - recorded, never retried: a send may have happened
        _receipt(db, item, "failed", f"Not unsubscribed: stopped on an error ({type(exc).__name__}).")
        return "failed"
    _receipt(db, item, "done", f"Unsubscribed: {detail}.")
    return "done"


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
            secret = password_path(credentials_dir, sid)
            reader, act, counts = None, None, {}
            try:
                for item in items[:limit]:
                    # read for every change, so turning dry runs back on takes effect at once
                    dry = settings.dry_run(config.store_dir, sid)
                    enabled = set() if dry else settings.enabled_types(config.store_dir, sid)
                    problem = _check(item)
                    if problem:
                        _receipt(db, item, "refused", problem)
                        counts["refused"] = counts.get("refused", 0) + 1
                        continue
                    body = item["body"]
                    if body["type"] in actions.SENDER_TYPES:
                        counts_key = _unsubscribe(db, item, source, secret, body["type"] in enabled)
                        counts[counts_key] = counts.get(counts_key, 0) + 1
                        continue
                    if body["type"] == "trash" and str(body.get("producer", "")).startswith("archive:"):
                        from towpath.mail import archive

                        problem = archive.check_copy(config.store_dir, body["target"].get("item_id"))
                        if problem:  # read back again now: no copy, no removal
                            _receipt(db, item, "skipped", f"Not removed: {problem}.")
                            counts["skipped"] = counts.get("skipped", 0) + 1
                            continue
                    live = body["type"] in enabled and secret.is_file()
                    try:
                        with _mapped_errors():
                            if live:
                                if act is None:
                                    act = connect_act(source, secret.read_text().strip())
                                act.sent = False
                                detail, before, after = _execute(
                                    act, body, trash_ok="trash" in enabled and not item.get("auto"))
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
                        if act is not None and act.sent:  # the command went out, then the connection dropped
                            _receipt(db, item, "uncertain", "The connection dropped while this change was being made; "
                                     "check the message in your mail app. It won't be tried again.")
                        raise  # the account or network stopped: what wasn't sent waits for the next poll
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
