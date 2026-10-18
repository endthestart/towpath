"""Restoring the owner's decisions from an export package (towpath.export) into this Towpath.

A restore only adds: a decision already made here is kept as it is, so restoring twice, or into a Towpath the owner
has used since, changes nothing they decided. It first shows what it would add (``plan``), and adds it when the owner
asks (``apply``). Only the owner's own decisions come back: reviews of events, recollections, memories kept or
dismissed, the profile, settled pairs, folders kept off Space's clutter list, per-item marks (who may see an item,
which models may read it), dismissed proposals, collections, questions asked, sender categories, conversation marks,
draft notes and rules. Each row is checked as strictly as the page that made it would check it, so a damaged or
hand-edited file can't leave a decision behind that breaks a page.

What could change mail, reach a model or widen access never comes back from a file: permissions for sources and
models, the model chosen for each task, finding events by itself, an account's dry-run switch and the changes it
allows, digests, labels and filing. Rules come back turned off, asking first, for the owner to review. Mail
decisions belong to an account, matched by its provider and name; an account not connected here yet is skipped
and can be restored later, once it is. Marks on a sender or a message are moved to the matching account's own IDs.
A photo collection comes back as its search: which photos the owner accepted is never written to a file, since a
photo may be hidden later. A review of an event found again under another account's ID starts unreviewed.
"""

import io
import json
import zipfile
import zlib
from contextlib import closing
from datetime import date

from towpath import decisions as item_settings, life, life_conflicts, life_groups, space
from towpath.decisions import now
from towpath.stores import open_store, store_path

VERSION = 1
WRITER = "web"
MAX_MEMBER = 16 * 1024 * 1024  # bytes of one file in the package, uncompressed
MAX_TOTAL = 32 * 1024 * 1024
MAIL_KEYS = {"draft_notes"}  # the only account settings restored: nothing that changes mail
TABLES = ("accounts", "settings", "dismissals", "collections", "collection_items", "questions", "sender_marks",
          "thread_marks")
LEFT_OUT = ("permissions for sources and models", "the model for each task", "finding events by itself",
            "each account's dry-run switch, allowed changes, digest, labels and filing")


class RestoreError(ValueError):
    pass


# -- what the export writes for a restore -----------------------------------------------------------------------


def _str(*values) -> bool:
    return all(isinstance(v, str) and v for v in values)


def _review(value) -> bool:
    if not isinstance(value, dict) or value.get("state") not in ("accepted", "rejected", "corrected", "proposed"):
        return False
    if not _str(value.get("by"), value.get("at")):
        return False
    correction = value.get("correction")
    if correction is None:
        return value["state"] != "corrected"
    if not isinstance(correction, dict) or not correction or set(correction) - {"statement", "when"}:
        return False
    if "statement" in correction and not _str(correction["statement"]):
        return False
    if "when" in correction:
        try:
            life.When.from_dict(correction["when"]).bounds()
        except Exception:  # noqa: BLE001 - any malformed date is refused
            return False
    return True


def _group(value) -> bool:
    return (isinstance(value, dict) and value.get("state") in ("kept", "dismissed") and
            _str(value.get("kind"), value.get("title"), value.get("by"), value.get("at")) and
            isinstance(value.get("members"), list) and all(_str(m) for m in value["members"]) and
            all(_str(m) for m in value.get("proposed_members") or []) and
            isinstance(value.get("proposed_members", []), list))


def _profile(value) -> bool:
    if not isinstance(value, dict) or set(value) - {"name", "birthday"}:
        return False
    if value.get("name") is not None and not (isinstance(value["name"], str) and len(value["name"]) <= 80):
        return False
    try:
        return value.get("birthday") is None or bool(date.fromisoformat(value["birthday"]))
    except (TypeError, ValueError):
        return False


def _loads(value: str):
    try:
        return json.loads(value)
    except (ValueError, RecursionError):
        return None


def restorable_setting(target: str, key: str, value: str) -> bool:
    """Whether a decisions-store setting is one of the owner's decisions a file may bring back, and is well formed."""
    if target.startswith("claim:"):
        if key == life.REVIEW_KEY:
            return _review(_loads(value))
        if key == life.RECOLLECTION_KEY:
            try:
                life.Claim.from_dict(json.loads(value))
                return True
            except (life.ClaimError, ValueError, KeyError, TypeError):
                return False
        return False
    if target.startswith("group:"):
        return key == life_groups.GROUP_KEY and _group(_loads(value))
    if (target, key) == (life_groups.PROFILE_TARGET, life_groups.PROFILE_KEY):
        return _profile(_loads(value))
    if target == life_conflicts.TARGET:
        return _loads(value) == "both-right"
    if target.startswith("space:"):
        return key == space.KEEP_KEY and value == space.KEEP_VALUE
    if key in item_settings.SETTINGS:
        return value in item_settings.SETTINGS[key]
    return False


def draft_notes(value: str) -> bool:
    from towpath.mail.threads import NOTES_MAX

    found = _loads(value)
    return isinstance(found, str) and len(found) <= NOTES_MAX


def collection_ok(kind, definition) -> bool:
    """A collection definition the collection pages can read."""
    from towpath.unified import collections
    from towpath.unified.contracts import Filters

    if kind not in collections.KINDS:
        return False
    if kind == "set":
        return definition is None
    if not isinstance(definition, str):
        return False
    try:
        found = json.loads(definition)
        if kind == "photos":
            return isinstance(found, dict) and collections.photo_filters(found) == found
        Filters.from_dict(found)
        return True
    except Exception:  # noqa: BLE001 - anything unreadable is refused
        return False


def _rows(db, sql: str, args=()) -> list[dict]:
    try:
        return [dict(r) for r in db.execute(sql, args)]
    except Exception:  # noqa: BLE001 - a store from before a table: nothing of it to export
        return []


def package(store_dir) -> dict[str, list]:
    """The files the export adds under ``restore/``."""
    out = {name: [] for name in TABLES}
    if store_path(store_dir, "connections").exists():
        with closing(open_store(store_dir, "connections", WRITER)) as db:
            out["accounts"] = _rows(db, "SELECT source_id, provider, display_name FROM connections ORDER BY source_id")
    if store_path(store_dir, "decisions").exists():
        with closing(open_store(store_dir, "decisions", WRITER)) as db:
            mail_targets = {f"mail:{a['source_id']}" for a in out["accounts"]}
            for r in _rows(db, "SELECT * FROM settings ORDER BY target_id, key"):
                if restorable_setting(r["target_id"], r["key"], r["value"]) or (
                        r["target_id"] in mail_targets and r["key"] in MAIL_KEYS):
                    out["settings"].append(r)
            for table in ("dismissals", "collections", "questions"):
                out[table] = _rows(db, f"SELECT * FROM {table}")
            # members of reference sets only: a photo collection's accepted photos are never written to a file
            out["collection_items"] = _rows(db, "SELECT * FROM collection_items WHERE role = 'member'")
        items = [r for r in out["settings"] if r["key"] in item_settings.SETTINGS and r["target_id"].startswith("itm_")]
        if items and store_path(store_dir, "source").exists():
            with closing(open_store(store_dir, "source", WRITER)) as src:
                for r in items:  # a message's mark moves with the message, whose ID is made from its account's
                    row = src.execute("SELECT source_id, native_id FROM items WHERE item_id = ?",
                                      (r["target_id"],)).fetchone()
                    if row:
                        r["item"] = {"source_id": row[0], "native_id": row[1]}
    if store_path(store_dir, "maildecisions").exists():
        with closing(open_store(store_dir, "maildecisions", WRITER)) as db:
            for table in ("sender_marks", "thread_marks"):
                out[table] = _rows(db, f"SELECT * FROM {table}")
    return out


# -- reading a package ------------------------------------------------------------------------------------------


def read(data: bytes) -> dict:
    """The restore part of an export package, checked; RestoreError when it isn't one."""
    try:
        return _read(data)
    except RestoreError:
        raise
    except (zipfile.BadZipFile, zlib.error, EOFError, RuntimeError, NotImplementedError, RecursionError,
            ValueError, OSError):  # damaged, encrypted, oddly compressed or absurdly nested
        raise RestoreError("That export is damaged and can't be read.") from None


def _read(data: bytes) -> dict:
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise RestoreError("That isn't a Towpath export (not a zip file).") from None
    with z:
        infos = {i.filename: i for i in z.infolist()}
        if "restore/version.json" not in infos:
            raise RestoreError("That zip has nothing to restore (it isn't a Towpath export, or is from before "
                               "restoring was possible).")
        total = 0

        def load(name, default):
            nonlocal total
            info = infos.get(name)
            if info is None:
                return default
            if info.file_size > MAX_MEMBER or total + info.file_size > MAX_TOTAL:
                raise RestoreError("That export is too large to restore.")
            total += info.file_size
            with z.open(info) as f:
                raw = f.read(MAX_MEMBER + 1)
            if len(raw) > MAX_MEMBER:
                raise RestoreError("That export is too large to restore.")
            try:
                return json.loads(raw)
            except (ValueError, RecursionError):
                raise RestoreError(f"{name} in that export can't be read.") from None

        version = load("restore/version.json", {})
        if not isinstance(version, dict) or version.get("version") != VERSION:
            raise RestoreError("That export is from a different version of Towpath and can't be restored here.")
        found = {name: load(f"restore/{name}.json", []) for name in TABLES}
        found["rules"] = {}
        for name in infos:
            parts = name.split("/")
            if len(parts) == 3 and parts[0] == "mail" and parts[2] == "rules.json":
                found["rules"][parts[1]] = load(name, [])
    for name, rows in found.items():
        if name != "rules" and not (isinstance(rows, list) and all(isinstance(r, dict) for r in rows)):
            raise RestoreError(f"restore/{name}.json in that export can't be read.")
    return found


# -- planning and applying --------------------------------------------------------------------------------------


def _accounts(store_dir, exported: list[dict]) -> dict[str, str | None]:
    """Each exported account's source here: the same provider and name, else the same ID and provider."""
    here = []
    if store_path(store_dir, "connections").exists():
        with closing(open_store(store_dir, "connections", WRITER)) as db:
            here = _rows(db, "SELECT source_id, provider, display_name FROM connections")
    out = {}
    for a in exported:
        match = next((h["source_id"] for h in here if (h["provider"], h["display_name"]) ==
                      (a.get("provider"), a.get("display_name"))), None) or next(
            (h["source_id"] for h in here if (h["source_id"], h["provider"]) ==
             (a.get("source_id"), a.get("provider"))), None)
        out[str(a.get("source_id"))] = match
    return out


def _s(row: dict, *keys) -> tuple:
    vals = tuple(row.get(k) for k in keys)
    if not _str(*vals):
        raise KeyError
    return vals


def _opt(row: dict, *keys) -> tuple:
    vals = tuple(row.get(k) for k in keys)
    if not all(v is None or isinstance(v, str) for v in vals):
        raise KeyError
    return vals


def _work(store_dir, found: dict) -> dict:
    """What a restore would add: rows per table, and counts of what it leaves alone."""
    from towpath.connect import item_id
    from towpath.mail import rules as mail_rules, senders, threads
    from towpath.unified.contracts import Reference

    accounts = _accounts(store_dir, found["accounts"])
    names = {str(a.get("source_id")): a.get("display_name") or a.get("source_id") for a in found["accounts"]}
    adds = {k: [] for k in ("settings", "dismissals", "collections", "collection_items", "questions",
                            "sender_marks", "thread_marks", "rules")}
    kept = skipped = 0
    waiting: set[str] = set()
    seen: set[tuple] = set()

    def account(sid):
        """The account here, or None: an account not connected here yet waits; one the package doesn't list is
        skipped."""
        nonlocal skipped
        if sid not in accounts:
            skipped += 1
        elif accounts[sid] is None:
            waiting.add(names.get(sid, sid))
        return accounts.get(sid)

    def once(*key) -> bool:
        """False for a row the file already held (counted once)."""
        if key in seen:
            return False
        seen.add(key)
        return True

    with closing(open_store(store_dir, "decisions", WRITER)) as d:
        for r in found["settings"]:
            try:
                target, key, value, author, at = _s(r, "target_id", "key", "value", "author", "at")
            except KeyError:
                skipped += 1
                continue
            if target.startswith("mail:"):
                if key not in MAIL_KEYS or not draft_notes(value):
                    skipped += 1
                    continue
                sid = account(target.split(":", 1)[1])
                if sid is None:
                    continue
                target = f"mail:{sid}"
            elif not restorable_setting(target, key, value):
                skipped += 1
                continue
            elif target.startswith("sender:"):  # sender:<account>:<address>
                parts = target.split(":", 2)
                if len(parts) != 3 or not parts[2]:
                    skipped += 1
                    continue
                sid = account(parts[1])
                if sid is None:
                    continue
                target = f"sender:{sid}:{parts[2]}"
            elif isinstance(r.get("item"), dict):  # a message's own mark: its ID is made from its account's
                native = r["item"]
                if not _str(native.get("source_id"), native.get("native_id")):
                    skipped += 1
                    continue
                sid = account(native["source_id"])
                if sid is None:
                    continue
                target = item_id(sid, native["native_id"])
            if not once("settings", target, key):
                continue
            if d.execute("SELECT 1 FROM settings WHERE target_id = ? AND key = ?", (target, key)).fetchone():
                kept += 1
            else:
                adds["settings"].append((target, key, value, author, at))
        for r in found["dismissals"]:
            try:
                match_key, author, at = _s(r, "match_key", "author", "at")
                proposal, reason = _opt(r, "proposal_id", "reason")
            except KeyError:
                skipped += 1
                continue
            if not once("dismissal", match_key):
                continue
            if d.execute("SELECT 1 FROM dismissals WHERE match_key = ?", (match_key,)).fetchone():
                kept += 1
            else:
                adds["dismissals"].append((match_key, proposal, reason, author, at))
        new_collections = {}
        for r in found["collections"]:
            try:
                cid, name, kind, author, created, updated = _s(r, "collection_id", "name", "kind", "author",
                                                               "created_at", "updated_at")
                (definition,) = _opt(r, "definition")
            except KeyError:
                skipped += 1
                continue
            if len(name) > 120 or not collection_ok(kind, definition):
                skipped += 1
                continue
            if not once("collection", cid) or not once("collection-name", name):
                continue
            if d.execute("SELECT 1 FROM collections WHERE collection_id = ? OR name = ?", (cid, name)).fetchone():
                kept += 1
            else:
                new_collections[cid] = kind
                adds["collections"].append((cid, name, kind, definition, author, created, updated))
        for r in found["collection_items"]:
            try:
                cid, ref, role, author, added = _s(r, "collection_id", "ref", "role", "author", "added_at")
                version, title, source_type, note = _opt(r, "version", "title", "source_type", "note")
                Reference.parse(ref)
            except Exception:  # noqa: BLE001 - a malformed row or reference is left out
                skipped += 1
                continue
            if role != "member" or new_collections.get(cid) != "set":
                skipped += role != "member"
                continue
            if once("item", cid, ref):
                adds["collection_items"].append((cid, ref, role, version, title, source_type, note, author, added))
        for r in found["questions"]:
            try:
                qid, text, author, at = _s(r, "question_id", "text", "author", "at")
            except KeyError:
                skipped += 1
                continue
            if len(text) > 2000 or not once("question", qid):
                skipped += len(text) > 2000
                continue
            if d.execute("SELECT 1 FROM questions WHERE question_id = ?", (qid,)).fetchone():
                kept += 1
            else:
                adds["questions"].append((qid, text, author, at))

    with closing(open_store(store_dir, "maildecisions", WRITER)) as m:
        for table, field, allowed in (("sender_marks", "address", senders.CATEGORIES),
                                      ("thread_marks", "thread_key", threads.STATES)):
            value_field = "category" if table == "sender_marks" else "state"
            for r in found[table]:
                try:
                    old_sid, name, value, author, at = _s(r, "source_id", field, value_field, "author", "at")
                except KeyError:
                    skipped += 1
                    continue
                if value not in allowed:
                    skipped += 1
                    continue
                sid = account(old_sid)
                if sid is None or not once(table, sid, name):
                    continue
                if m.execute(f"SELECT 1 FROM {table} WHERE source_id = ? AND {field} = ?", (sid, name)).fetchone():
                    kept += 1
                else:
                    adds[table].append((sid, name, value, author, at))
    for old_sid, listed in (found["rules"] or {}).items():
        sid = account(old_sid) if isinstance(listed, list) else None
        if sid is None:
            continue
        have = [r["body"] for r in mail_rules.all_rules(store_dir, sid, WRITER)]
        for r in listed:
            body = r.get("body") if isinstance(r, dict) else None
            try:
                clean = mail_rules.validate(body)
            except Exception:  # noqa: BLE001 - a rule the current rules refuse is left out
                skipped += 1
                continue
            if clean in have:
                kept += 1
            else:
                have.append(clean)
                adds["rules"].append((sid, clean))
    return {"adds": adds, "kept": kept, "skipped": skipped, "waiting": sorted(map(str, waiting))}


LABELS = {"settings": "decisions on events, memories, items and folders", "dismissals": "dismissed proposals",
          "collections": "collections", "questions": "questions you asked", "sender_marks": "sender categories",
          "thread_marks": "conversation marks", "rules": "rules (turned off, asking first)"}


def summary(work: dict) -> dict:
    return {"adds": [(LABELS[k], len(v)) for k, v in work["adds"].items() if k in LABELS and v],
            "total": sum(len(v) for k, v in work["adds"].items() if k != "collection_items"),
            "kept": work["kept"], "skipped": work["skipped"], "waiting": work["waiting"], "left_out": LEFT_OUT}


def plan(store_dir, data: bytes) -> dict:
    """What restoring this package would add; nothing is changed."""
    return summary(_work(store_dir, read(data)))


def apply(store_dir, data: bytes, author: str = "owner") -> dict:
    """Add what the package holds that isn't decided here yet; the same summary as ``plan``."""
    from towpath.mail import rules as mail_rules

    work = _work(store_dir, read(data))
    adds = work["adds"]
    with closing(open_store(store_dir, "decisions", WRITER)) as d:
        d.executemany("INSERT OR IGNORE INTO settings (target_id, key, value, author, at) VALUES (?,?,?,?,?)",
                      adds["settings"])
        d.executemany("INSERT OR IGNORE INTO dismissals (match_key, proposal_id, reason, author, at) "
                      "VALUES (?,?,?,?,?)", adds["dismissals"])
        d.executemany("INSERT OR IGNORE INTO collections (collection_id, name, kind, definition, author, created_at, "
                      "updated_at) VALUES (?,?,?,?,?,?,?)", adds["collections"])
        d.executemany("INSERT OR IGNORE INTO collection_items (collection_id, ref, role, version, title, source_type, "
                      "note, author, added_at) VALUES (?,?,?,?,?,?,?,?,?)", adds["collection_items"])
        d.executemany("INSERT OR IGNORE INTO questions (question_id, text, author, at) VALUES (?,?,?,?)",
                      adds["questions"])
        result = summary(work)
        d.execute("INSERT INTO decision_log (kind, target_id, detail, author, at) "
                  "VALUES ('restore', 'towpath', ?, ?, ?)",
                  (json.dumps({k: n for k, n in result["adds"]}, sort_keys=True), author, now()))
        d.commit()
    with closing(open_store(store_dir, "maildecisions", WRITER)) as m:
        m.executemany("INSERT OR IGNORE INTO sender_marks VALUES (?,?,?,?,?)", adds["sender_marks"])
        m.executemany("INSERT OR IGNORE INTO thread_marks VALUES (?,?,?,?,?)", adds["thread_marks"])
        m.commit()
    for sid, body in adds["rules"]:
        mail_rules.save(store_dir, sid, body, enabled=False, mode="propose", author=author)
    return result
