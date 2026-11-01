"""Matching mail against rules: by header, by the model, and a preview before a rule runs."""

import fnmatch
import json
import time
from contextlib import closing

from towpath.canonical import digest
from towpath.mail import actions
from towpath.mail.rules.common import BATCH, INSTRUCTION, MATCH_SCHEMA_BASE, PROMPT, ROLE, SLICE_SECONDS, TASK, now
from towpath.mail.rules.owner_rules import all_rules, producer, validate
from towpath.stores import open_store, store_path


def _like(pattern: str, *values) -> bool:
    pattern = pattern.lower()
    values = [str(v).lower() for v in values if v]
    if any(ch in pattern for ch in "*?["):
        return any(fnmatch.fnmatchcase(v, pattern) for v in values)
    return any(pattern in v for v in values)


def static_match(rule_body: dict, message: dict, category: str) -> bool:
    c = rule_body["conditions"]
    if c.get("mailbox") and (message.get("mailbox") or "").lower() != c["mailbox"].lower():
        return False
    if c.get("from") and not _like(c["from"], message.get("from_addr"), message.get("from_name")):
        return False
    if c.get("to") and not _like(c["to"], *(message.get("to_addrs") or []), *(message.get("cc_addrs") or [])):
        return False
    if c.get("subject") and not _like(c["subject"], message.get("subject")):
        return False
    if c.get("body") and not _like(c["body"], message.get("text")):
        return False
    if c.get("categories") and category not in c["categories"]:
        return False
    return True


def preview(store_dir, source_id: str, body: dict, limit: int = 200, role: str = "web") -> list[dict]:
    """Which of the latest messages in the rule's mailbox its static conditions match (no model, nothing saved)."""
    from towpath.mail import senders

    clean = validate(body)
    cats = senders.categories(store_dir, source_id, role)
    box = clean["conditions"].get("mailbox") or "INBOX"
    with closing(open_store(store_dir, "mail", role)) as db:
        rows = [dict(r) for r in db.execute(
            "SELECT item_id, mailbox, from_addr, from_name, to_addrs, cc_addrs, subject, text, list_id, date_utc "
            "FROM messages WHERE source_id = ? AND lower(mailbox) = lower(?) ORDER BY date_utc DESC LIMIT ?",
            (source_id, box, limit))]
    out = []
    for m in rows:
        m["to_addrs"], m["cc_addrs"] = json.loads(m["to_addrs"]), json.loads(m["cc_addrs"])
        category = senders.message_category(m, cats.get(m["from_addr"]))
        if static_match(clean, m, category):
            out.append({"item_id": m["item_id"], "subject": m["subject"], "from": m["from_addr"],
                        "date": m["date_utc"], "category": category})
    return out


def _run_state(intel, source_id: str, rule: dict, mail) -> dict:
    row = intel.execute("SELECT watermark, examined, matched FROM rule_runs WHERE source_id = ? AND rule_id = ? AND "
                        "version = ?", (source_id, rule["rule_id"], rule["version"])).fetchone()
    if row:
        return {"watermark": row[0], "examined": row[1], "matched": row[2]}
    start = 0 if rule["body"]["scope"] == "all" else (
        mail.execute("SELECT coalesce(max(rowid), 0) FROM messages WHERE source_id = ?", (source_id,)).fetchone()[0])
    intel.execute("INSERT INTO rule_runs VALUES (?,?,?,?,?,?,?)",
                  (source_id, rule["rule_id"], rule["version"], start, 0, 0, now()))
    intel.commit()
    return {"watermark": start, "examined": 0, "matched": 0}


def _propose(plan, intel, source_id: str, rule: dict, message: dict, reason: str, by: str) -> list[str]:
    ids = []
    for a in rule["body"]["actions"]:
        body = actions.make(source_id, a["type"], actions.message_target(message), a["params"],
                            reason=f"Rule “{rule['body']['name']}”: {reason}", producer=producer(rule))
        pid = actions.proposal_id(body)
        plan.execute("INSERT OR IGNORE INTO proposals (proposal_id, source_id, body, digest, created_at) "
                     "VALUES (?,?,?,?,?)", (pid, source_id, json.dumps(body, sort_keys=True), digest(body), now()))
        ids.append(pid)
    intel.execute("INSERT OR REPLACE INTO rule_matches VALUES (?,?,?,?,?,?,?,?,?)",
                  (source_id, rule["rule_id"], rule["version"], message["item_id"], 1, by, reason, json.dumps(ids),
                   now()))
    return ids


def _ask_model(config, rules_: list[dict], message: dict) -> dict:
    from towpath.models import gateway

    options = {r["rule_id"]: r["body"]["instructions"] for r in rules_}
    schema = json.loads(json.dumps(MATCH_SCHEMA_BASE))
    schema["properties"]["rule"] = {"type": "string", "enum": [*options, "none"]}
    listed = "\n".join(f"- {rid}: {text}" for rid, text in options.items())
    email = (f"From: {message.get('from_name') or ''} <{message.get('from_addr') or ''}>\n"
             f"Subject: {message.get('subject') or ''}\n\n{(message.get('text') or '')[:4000]}")
    prompt = f"{INSTRUCTION}\n\nRules:\n{listed}\n\n<email>\n{email}\n</email>"
    from towpath.mail.privacy import targets

    return gateway.infer(config, TASK, prompt, "content", schema,
                         targets(message["source_id"], [message["item_id"]], [message.get("from_addr")]),
                         prompt_version=PROMPT, background=True)


def evaluate_pending(config, slice_seconds: float = SLICE_SECONDS, clock=time.monotonic) -> list[dict]:
    """One bounded slice per managed account: each enabled rule looks at messages it hasn't seen yet."""
    from towpath.mail import analysis, senders, threads

    summaries = []
    for sid in analysis.eligible(config):
        rules_ = [r for r in all_rules(config.store_dir, sid) if r["enabled"]]
        if not rules_ or not store_path(config.store_dir, "mail").exists():
            continue
        deadline = clock() + slice_seconds
        intel = open_store(config.store_dir, "mailintel", ROLE)
        mail = open_store(config.store_dir, "mail", ROLE)
        plan = open_store(config.store_dir, "mailplan", ROLE)
        src = open_store(config.store_dir, "source", ROLE)
        model_ok = threads.blocked(config, sid, TASK) is None
        matched = examined = 0
        stopped = None
        try:
            cats = senders.categories(config.store_dir, sid, ROLE)
            states = {r["rule_id"]: _run_state(intel, sid, r, mail) for r in rules_}
            if model_ok and any(r["body"]["instructions"] for r in rules_):
                model_ok = threads.ready(config, TASK)
            while clock() < deadline and stopped is None:
                low = min(s["watermark"] for s in states.values())
                rows = [dict(r) for r in mail.execute(
                    "SELECT rowid, * FROM messages WHERE source_id = ? AND rowid > ? ORDER BY rowid LIMIT ?",
                    (sid, low, BATCH))]
                if not rows:
                    break
                present = {r[0] for r in src.execute(
                    f"SELECT item_id FROM items WHERE absent_since_run IS NULL AND item_id IN "
                    f"({','.join('?' for _ in rows)})", [r["item_id"] for r in rows])}
                for m in rows:
                    if clock() >= deadline:
                        stopped = "deadline"
                        break
                    m["to_addrs"], m["cc_addrs"] = json.loads(m["to_addrs"]), json.loads(m["cc_addrs"])
                    due = [r for r in rules_ if states[r["rule_id"]]["watermark"] < m["rowid"]]
                    if m["item_id"] in present and due:
                        category = senders.message_category(m, cats.get(m["from_addr"]))
                        passed = [r for r in due if static_match(r["body"], m, category)]
                        for r in passed:
                            if not r["body"]["instructions"]:
                                _propose(plan, intel, sid, r, m, "Matched its conditions.", "static")
                                states[r["rule_id"]]["matched"] += 1
                                matched += 1
                        worded = [r for r in passed if r["body"]["instructions"]]
                        if worded and not model_ok:
                            stopped = "no-model"  # wait for a model rather than skip these messages
                            break
                        if worded:
                            result = _ask_model(config, worded, m)
                            if result["status"] == "ok" and result["output"]["rule"] != "none":
                                chosen = next(r for r in worded if r["rule_id"] == result["output"]["rule"])
                                _propose(plan, intel, sid, chosen, m, result["output"]["reason"].strip()[:160],
                                         "model")
                                states[chosen["rule_id"]]["matched"] += 1
                                matched += 1
                            elif result["status"] != "ok" and threads._failure(result):
                                stopped = threads._failure(result)
                                break
                        examined += 1
                    for r in due:
                        states[r["rule_id"]]["watermark"] = m["rowid"]
                        states[r["rule_id"]]["examined"] += 1
                for rid, s in states.items():
                    version = next(r["version"] for r in rules_ if r["rule_id"] == rid)
                    intel.execute("UPDATE rule_runs SET watermark = ?, examined = ?, matched = ?, updated_at = ? "
                                  "WHERE source_id = ? AND rule_id = ? AND version = ?",
                                  (s["watermark"], s["examined"], s["matched"], now(), sid, rid, version))
                intel.commit()
                plan.commit()
            if examined or matched:
                summaries.append({"source_id": sid, "step": "mail-rules", "examined": examined, "matched": matched,
                                  **({"stopped": stopped} if stopped not in (None, "deadline", "paced") else {})})
        finally:
            for db in (intel, mail, plan, src):
                db.close()
    return summaries


def history(store_dir, rule_id: str, role: str = "web") -> dict:
    """A rule's runs and matches, with what the owner did with its proposals."""
    from towpath.mail import settings as mail_settings

    out = {"runs": [], "matches": [], "approved": 0, "rejected": 0}
    if not store_path(store_dir, "mailintel").exists():
        return out
    answers = mail_settings.answers(store_dir, role=role)
    with closing(open_store(store_dir, "mailintel", role)) as db:
        out["runs"] = [dict(r) for r in db.execute("SELECT * FROM rule_runs WHERE rule_id = ? ORDER BY version DESC",
                                                   (rule_id,))]
        for r in db.execute("SELECT * FROM rule_matches WHERE rule_id = ? AND matched = 1 ORDER BY at DESC LIMIT 200",
                            (rule_id,)):
            d = dict(r)
            d["proposals"] = json.loads(d["proposals"])
            decided = [answers[p]["decision"] for p in d["proposals"] if p in answers]
            out["approved"] += decided.count("approved")
            out["rejected"] += decided.count("rejected")
            out["matches"].append(d)
    return out
