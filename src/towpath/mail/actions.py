"""What a mailbox change is: its types, the document that describes one, and how it is undone.

A change is a frozen JSON document (``towpath.mail.action/1``). Its digest is what the owner approves and what the
action runner checks before acting, so a change can't be altered between approval and execution. A message is
named by its IMAP identity (mailbox, UIDVALIDITY, UID) and, where known, its Message-ID, which is checked too; a
change made by Undo may name only the Message-ID, because a moved message has a new UID.

Tiers follow docs/mail-management.md. Only reversible types may ever run without a per-batch approval (``auto``);
moving to Trash never may. Nothing here expunges or permanently deletes: Trash is emptied by the provider's own
retention.
"""

import re
from email import message_from_string, policy

from towpath.canonical import digest

SCHEMA = "towpath.mail.action/1"
KEYWORD = re.compile(r"^\$?[A-Za-z0-9_.-]{1,64}$")
MAX_APPEND_BYTES = 1_000_000

# type: label shown to the owner, tier, whether a rule may ever do it without asking, the inverse type.
TYPES = {
    "mark_read": {"label": "Mark as read", "tier": 1, "auto": True, "inverse": "mark_unread"},
    "mark_unread": {"label": "Mark as unread", "tier": 1, "auto": True, "inverse": "mark_read"},
    "flag": {"label": "Flag", "tier": 1, "auto": True, "inverse": "unflag"},
    "unflag": {"label": "Remove flag", "tier": 1, "auto": True, "inverse": "flag"},
    "add_keyword": {"label": "Add label", "tier": 1, "auto": True, "inverse": "remove_keyword"},
    "remove_keyword": {"label": "Remove label", "tier": 1, "auto": True, "inverse": "add_keyword"},
    "archive": {"label": "Archive", "tier": 1, "auto": True, "inverse": "move"},
    "move": {"label": "Move", "tier": 1, "auto": True, "inverse": "move"},
    "draft": {"label": "Save a draft", "tier": 1, "auto": True, "inverse": "trash"},
    "inbox_note": {"label": "Put a note in the inbox", "tier": 1, "auto": True, "inverse": "trash"},
    "trash": {"label": "Move to Trash", "tier": 4, "auto": False, "inverse": "move"},
    "unsubscribe": {"label": "Unsubscribe", "tier": 3, "auto": False, "inverse": None},
}
FLAG_TYPES = {"mark_read": ("+", "\\Seen"), "mark_unread": ("-", "\\Seen"), "flag": ("+", "\\Flagged"),
              "unflag": ("-", "\\Flagged")}
MOVE_TYPES = {"archive", "move", "trash"}
APPEND_TYPES = {"draft": "drafts", "inbox_note": "inbox"}
SENDER_TYPES = {"unsubscribe"}  # aimed at a sender, not at a message in a mailbox


class ActionError(ValueError):
    pass


def proposal_id(body: dict) -> str:
    return "mact_" + digest(body)[:20]


def make(source_id: str, kind: str, target: dict, params: dict | None = None, reason: str = "",
         producer: str = "owner") -> dict:
    body = {"schema": SCHEMA, "source_id": source_id, "type": kind, "target": target, "params": params or {},
            "reason": reason, "producer": producer}
    validate(body)
    return body


def message_target(record: dict) -> dict:
    """The target for a message the mail store holds."""
    return {"item_id": record["item_id"], "mailbox": record["mailbox"], "uidvalidity": record["uidvalidity"],
            "uid": record["uid"], "message_id": record.get("message_id")}


def validate(body: dict) -> None:
    if not isinstance(body, dict) or body.get("schema") != SCHEMA:
        raise ActionError("not a mail action")
    kind = body.get("type")
    if kind not in TYPES:
        raise ActionError(f"unknown action type {kind!r}")
    target, params = body.get("target"), body.get("params")
    if not isinstance(target, dict) or not isinstance(params, dict) or not body.get("source_id"):
        raise ActionError("an action needs a source, a target and parameters")
    if kind in SENDER_TYPES:
        if not str(target.get("sender") or "").count("@") == 1:
            raise ActionError("an unsubscribe names the sender's address")
        method, url = params.get("method"), str(params.get("url") or "")
        if not ((method == "post" and url.startswith("https://")) or (method == "mail" and url.startswith("mailto:"))):
            raise ActionError("an unsubscribe is a one-click https request or a mailto address")
        return
    if kind in APPEND_TYPES:
        raw = params.get("message")
        if not isinstance(raw, str) or not raw.strip() or len(raw.encode("utf-8")) > MAX_APPEND_BYTES:
            raise ActionError("a draft or note needs a message under 1 MB")
        if not message_from_string(raw, policy=policy.default).get("Message-ID"):
            raise ActionError("a draft or note needs a Message-ID, so it can be found again for Undo")
        return
    if not isinstance(target.get("mailbox"), str) or not target["mailbox"]:
        raise ActionError("the target needs a mailbox")
    has_uid = isinstance(target.get("uid"), int) and isinstance(target.get("uidvalidity"), int)
    if not has_uid and not target.get("message_id"):
        raise ActionError("the target needs a UID and UIDVALIDITY, or a Message-ID")
    if kind in ("add_keyword", "remove_keyword") and not KEYWORD.match(str(params.get("keyword", ""))):
        raise ActionError("a label is 1 to 64 letters, digits, '.', '_' or '-'")
    if kind == "move" and not isinstance(params.get("mailbox"), str):
        raise ActionError("a move needs a destination mailbox")


def describe(body: dict) -> str:
    kind, params = body["type"], body["params"]
    if kind in ("add_keyword", "remove_keyword"):
        return f"{TYPES[kind]['label']} “{params['keyword']}”"
    if kind == "move":
        return f"Move to {params['mailbox']}"
    if kind == "unsubscribe":
        return f"Unsubscribe from {body['target']['sender']}"
    return TYPES[kind]["label"]


def appended_message_id(body: dict) -> str | None:
    return message_from_string(body["params"]["message"], policy=policy.default).get("Message-ID")


def inverse(body: dict, before: dict, after: dict) -> dict | None:
    """The change that undoes ``body`` as it was carried out, or None when there is nothing to undo."""
    kind, target = body["type"], body["target"]
    undo = TYPES[kind]["inverse"]
    producer = f"undo:{proposal_id(body)}"
    if kind in FLAG_TYPES:
        sign, flag = FLAG_TYPES[kind]
        if (flag in before.get("flags", ())) == (sign == "+"):
            return None  # it already was that way: undoing must not change it
        return make(body["source_id"], undo, target, {}, f"Undo: {describe(body)}", producer)
    if kind in ("add_keyword", "remove_keyword"):
        if (body["params"]["keyword"] in before.get("flags", ())) == (kind == "add_keyword"):
            return None
        return make(body["source_id"], undo, target, dict(body["params"]), f"Undo: {describe(body)}", producer)
    if kind in MOVE_TYPES:
        message_id = target.get("message_id") or before.get("message_id")
        if not message_id or not after.get("mailbox"):
            return None
        return make(body["source_id"], "move", {"mailbox": after["mailbox"], "message_id": message_id},
                    {"mailbox": before["mailbox"]}, f"Undo: {describe(body)}", producer)
    if kind in APPEND_TYPES and after.get("mailbox"):
        return make(body["source_id"], "trash", {"mailbox": after["mailbox"], "message_id": appended_message_id(body)},
                    {}, f"Undo: {describe(body)}", producer)
    return None
