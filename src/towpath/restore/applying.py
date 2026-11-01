"""Planning and applying a restore: only what isn't there already, and only for accounts connected here."""

import json
from contextlib import closing

from towpath.decisions import now
from towpath.restore.checks import collection_ok, draft_notes, restorable_setting, _str
from towpath.restore.common import LEFT_OUT, MAIL_KEYS, WRITER
from towpath.restore.exporting import _rows
from towpath.restore.reading import read
from towpath.stores import open_store, store_path


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


def _joins_clash(d, address: str, person: str) -> bool:
    """Whether joining ``address`` to ``person`` would contradict the joins here, or make a loop or a chain."""
    here = {t.split(":", 1)[1]: json.loads(v) for t, v in d.execute(
        "SELECT target_id, value FROM settings WHERE key = 'person' AND target_id LIKE 'address:%'")}
    return address in here or person in here or address in here.values()


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
            if target.startswith("address:") and _joins_clash(d, target.split(":", 1)[1], json.loads(value)):
                kept += 1  # a join here already decides who this address (or that person) is
                continue
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
