"""Rules as the owner writes them (web role), and which changes they approve."""

import json
import sqlite3
import uuid
from contextlib import closing

from towpath.mail import actions
from towpath.mail.rules.common import FIELDS, INBOX_ZERO, MODES, ROLE, SCOPES, RuleError, now
from towpath.stores import open_store, store_path


def add_inbox_zero(store_dir, source_id: str, author: str = "owner") -> int:
    """Add the rules rewritten from Inbox Zero to an account, switched off for the owner to review. Rules already
    there (the same conditions and actions) aren't added twice. Returns how many were added."""
    have = [r["body"] for r in all_rules(store_dir, source_id, "web")]
    added = 0
    for body in INBOX_ZERO:
        clean = validate(body)
        if clean not in have:
            save(store_dir, source_id, clean, enabled=False, mode="propose", author=author)
            have.append(clean)
            added += 1
    return added


def validate(body: dict) -> dict:
    """A clean copy of a rule body, or RuleError with a reason a person can act on."""
    from towpath.mail.senders import CATEGORIES

    if not isinstance(body, dict) or not str(body.get("name", "")).strip():
        raise RuleError("Give the rule a name.")
    cond = body.get("conditions") or {}
    clean = {k: str(cond[k]).strip() for k in FIELDS if str(cond.get(k) or "").strip()}
    cats = [c for c in cond.get("categories") or [] if c]
    if set(cats) - set(CATEGORIES):
        raise RuleError("Choose categories from the list.")
    if cats:
        clean["categories"] = sorted(set(cats))
    if str(cond.get("mailbox") or "").strip():
        clean["mailbox"] = str(cond["mailbox"]).strip()
    instructions = str(body.get("instructions") or "").strip()[:500]
    if not clean and not instructions:
        raise RuleError("Give the rule at least one condition, or describe in words which mail it is for.")
    acts = []
    for a in body.get("actions") or []:
        if not a or not a.get("type"):
            continue
        if a["type"] not in actions.TYPES or a["type"] in actions.APPEND_TYPES or a["type"] in actions.SENDER_TYPES:
            raise RuleError("Choose actions from the list.")
        params = {k: str(v).strip() for k, v in (a.get("params") or {}).items() if str(v).strip()}
        actions.validate({"schema": actions.SCHEMA, "source_id": "x", "type": a["type"],
                          "target": {"mailbox": "INBOX", "uid": 1, "uidvalidity": 1}, "params": params,
                          "reason": "", "producer": "check"})
        acts.append({"type": a["type"], "params": params})
    if not acts:
        raise RuleError("Choose at least one action.")
    scope = body.get("scope") if body.get("scope") in SCOPES else "new"
    return {"name": str(body["name"]).strip()[:80], "conditions": clean, "instructions": instructions or None,
            "actions": acts, "scope": scope}


def _latest(db, source_id: str | None = None) -> list[dict]:
    rows = db.execute("SELECT r.* FROM rules r JOIN (SELECT rule_id, max(revision) AS n FROM rules GROUP BY rule_id) "
                      "m ON r.rule_id = m.rule_id AND r.revision = m.n" +
                      (" WHERE r.source_id = ?" if source_id else "") + " ORDER BY r.position, r.at",
                      (source_id,) if source_id else ())
    out = []
    for r in rows:
        d = dict(r)
        d["body"] = json.loads(d["body"])
        if not d["deleted"]:
            out.append(d)
    return out


def all_rules(store_dir, source_id: str | None = None, role: str = ROLE) -> list[dict]:
    """The current version of each rule (deleted ones left out), in the owner's order."""
    if not store_path(store_dir, "maildecisions").exists():
        return []
    try:
        with closing(open_store(store_dir, "maildecisions", role)) as db:
            return _latest(db, source_id)
    except sqlite3.OperationalError:  # a store from before rules; the web service adds the table
        return []


def get(store_dir, rule_id: str, role: str = "web") -> dict | None:
    return next((r for r in all_rules(store_dir, role=role) if r["rule_id"] == rule_id), None)


def save(store_dir, source_id: str, body: dict, rule_id: str | None = None, enabled: bool | None = None,
         mode: str | None = None, position: int | None = None, deleted: bool = False, author: str = "owner") -> dict:
    """Save a new version of a rule (or a new rule). Returns the saved rule."""
    current = get(store_dir, rule_id) if rule_id else None
    if rule_id and current is None:
        raise RuleError("That rule no longer exists.")
    clean = validate(body) if not deleted else (current or {}).get("body")
    mode = mode or (current or {}).get("mode") or "propose"
    if mode not in MODES:
        raise RuleError("Unknown mode.")
    to_trash = any(a["type"] == "move" and str(a["params"].get("mailbox", "")).strip().lower() in actions.TRASH_NAMES
                   for a in clean["actions"])
    if mode == "auto" and (to_trash or any(not actions.TYPES[a["type"]]["auto"] for a in clean["actions"])):
        raise RuleError("A rule that moves mail to Trash always asks you first.")
    with closing(open_store(store_dir, "maildecisions", "web")) as db:
        if current is None:
            rule_id = "rule_" + uuid.uuid4().hex[:12]
            top = db.execute("SELECT coalesce(max(position), 0) FROM rules WHERE source_id = ?", (source_id,))
            position = position if position is not None else top.fetchone()[0] + 1
            version, revision = 1, 1
        else:
            version = current["version"] + (clean != current["body"])
            revision = current["revision"] + 1
            position = current["position"] if position is None else position
        db.execute("INSERT INTO rules (rule_id, revision, source_id, version, body, enabled, mode, position, deleted, "
                   "author, at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                   (rule_id, revision, source_id, version, json.dumps(clean, sort_keys=True),
                    int(enabled if enabled is not None else (current or {}).get("enabled", 1)), mode, position,
                    int(deleted), author, now()))
        db.commit()
    return get(store_dir, rule_id) if not deleted else {"rule_id": rule_id, "deleted": True}


def change(store_dir, rule_id: str, author: str = "owner", **what) -> dict:
    """Enable or disable, switch mode, move, or delete a rule, keeping its conditions and actions."""
    current = get(store_dir, rule_id)
    if current is None:
        raise RuleError("That rule no longer exists.")
    return save(store_dir, current["source_id"], current["body"], rule_id, author=author, **what)


def producer(rule: dict) -> str:
    return f"rule:{rule['rule_id']}@{rule['version']}"


def auto_approved(store_dir, body: dict, role: str = "act", cache: dict | None = None) -> bool:
    """Whether a proposal came from the current version of an enabled rule the owner switched to auto, and is a
    kind of change that may run without asking."""
    found = str(body.get("producer", ""))
    if not found.startswith("rule:") or not actions.TYPES.get(body.get("type"), {}).get("auto"):
        return False
    rule_id, _, version = found[5:].partition("@")
    if cache is None or "rules" not in cache:
        listed = {r["rule_id"]: r for r in all_rules(store_dir, role=role)}
        if cache is not None:
            cache["rules"] = listed
    else:
        listed = cache["rules"]
    rule = listed.get(rule_id)
    return bool(rule and rule["enabled"] and rule["mode"] == "auto" and str(rule["version"]) == version)
