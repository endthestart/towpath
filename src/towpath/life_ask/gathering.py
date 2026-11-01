"""Gathering the records that might answer a question, only from what the owner allowed."""

import json
import re
from contextlib import closing

from towpath import life
from towpath.life_ask.asking import MAX_ATTACHMENTS, MAX_CLAIMS, MAX_FILES, MAX_MESSAGES, MAX_PURCHASES, STOP, WRITER
from towpath.stores import open_store, store_path


def words(text: str) -> list[str]:
    return [w for w in re.findall(r"[\w'-]+", text.lower()) if w not in STOP and len(w) > 2]


def evidence(config, question: str, role: str = WRITER) -> list[dict]:
    """Numbered sources for a question: the best-matching events, then matching messages from allowed accounts."""
    from towpath.mail import search as mail_search
    from towpath.unified import grants

    terms = set(words(question))
    scored = []
    for c in life.claims(config.store_dir, limit=10**6, role=role):
        if c.state == "rejected":
            continue
        hits = len(terms & set(words(c.statement)))
        if hits:
            scored.append((hits, c))
    scored.sort(key=lambda x: (-x[0], x[1].when.earliest or ""))
    from towpath.mail import privacy

    picked = [c for _, c in scored[:MAX_CLAIMS]]
    named = privacy.ref_targets(config.store_dir, [cit.ref for c in picked for cit in c.citations], role)
    out = [{"kind": "event", "claim_id": c.claim_id, "text": c.statement, "date": c.when.expression,
            "quote": c.citations[0].captured_excerpt if c.citations else None,
            "ref": c.citations[0].ref if c.citations else None,
            "targets": sorted({t for cit in c.citations for t in named.get(cit.ref, [])})} for c in picked]
    allowed = [sid for sid, f in grants.granted(config.store_dir).items() if "life-evidence" in f]
    found = []
    for sid in allowed:
        for term_set in ([" ".join(sorted(terms))], sorted(terms)):  # all words first, then any one of them
            for q in term_set:
                for r in mail_search.search(config.store_dir, sid, q, limit=MAX_MESSAGES, role=role):
                    if r["item_id"] not in {f["item_id"] for f in found}:
                        found.append({**r, "source_id": sid})
            if len(found) >= MAX_MESSAGES:
                break
    for r in found[:MAX_MESSAGES]:
        out.append({"kind": "message", "item_id": r["item_id"], "source_id": r["source_id"],
                    "text": f"{r['subject'] or '(no subject)'} — "
                    f"{r['snippet']}", "date": (r["date_utc"] or "")[:10], "from": r["from_addr"],
                    "ref": f"mail:{r['item_id']}"})
    out += _years(config, question, set(allowed), role)
    out += _purchases(config, terms, allowed, role)
    out += _attachments(config, terms, allowed, role)
    out += _files(config, terms, allowed, role)
    return out


def _years(config, question: str, allowed: set[str], role: str) -> list[dict]:
    """For a question naming a year: that year's photos, mail, the people written to most and first, as one source."""
    from towpath import life_today

    out = []
    for year in sorted(set(re.findall(r"\b(19[5-9]\d|20\d\d)\b", question)))[:2]:
        y = life_today.one_year(config.store_dir, year, role=role, sources=allowed)
        t = y["totals"]
        if not (t["photos"] or t["received"] or t["sent"]):
            continue
        bits = [f"{t['photos']} photos taken", f"{t['received']} emails received and {t['sent']} sent"]
        if y["people"]:
            bits.append("wrote most to " + ", ".join(f"{a} ({n})" for a, n in y["people"][:5]))
        if y["first"]:
            bits.append("first wrote to " + ", ".join(f"{a} ({d})" for d, a in y["first"][:5]))
        if y["days"]:
            bits.append("most photos on " + ", ".join(f"{d} ({n})" for d, n in y["days"][:3]))
        from towpath.mail.privacy import targets

        named = [a for a, _ in y["people"][:5]] + [a for _, a in y["first"][:5]]
        out.append({"kind": "year", "text": "; ".join(bits), "date": year, "ref": None,
                    "targets": sorted({t for sid in allowed for t in targets(sid, [], named)})})
    return out


def _purchases(config, terms: set[str], allowed: list[str], role: str) -> list[dict]:
    """Purchases in allowed accounts whose merchant or items match a word of the question."""
    from towpath.mail import purchases

    out, seen = [], set()
    for sid in allowed:
        for w in [w for w in sorted(terms, key=len, reverse=True) if len(w) >= 3][:4]:
            for r in purchases.listing(config.store_dir, sid, w, limit=MAX_PURCHASES, role=role):
                if r["item_id"] in seen:
                    continue
                seen.add(r["item_id"])
                with closing(open_store(config.store_dir, "mail", role)) as mail:
                    found = mail.execute("SELECT from_addr FROM messages WHERE item_id = ?", (r["item_id"],)).fetchone()
                total = f", {r['total']:.2f} {r['currency'] or ''}".rstrip() if r["total"] is not None else ""
                bought = ", ".join(r["items"][:5]) or "items not named"
                out.append({"kind": "purchase", "item_id": r["item_id"], "source_id": sid, "date": r["day"] or "",
                            "from": found[0] if found else None,
                            "text": f"Bought from {r['merchant']}{total}: {bought}", "ref": f"mail:{r['item_id']}"})
    return out[:MAX_PURCHASES]


def _attachments(config, terms: set[str], allowed: list[str], role: str) -> list[dict]:
    """Attachments in allowed accounts whose names (or their message's subject) match the question."""
    from towpath.mail import attachments

    words = [w for w in sorted(terms, key=len, reverse=True) if len(w) >= 4][:3]
    out, seen = [], set()
    for sid in allowed:
        for q in ([" ".join(words)] if len(words) > 1 else []) + words:  # all words first, then each
            for r in attachments.listing(config.store_dir, sid, {"q": q}, limit=MAX_ATTACHMENTS, role=role):
                if r["ref"] in seen:
                    continue
                seen.add(r["ref"])
                out.append({"kind": "attachment", "item_id": r["item_id"], "source_id": sid,
                            "text": f"{r['filename'] or '(unnamed)'} — attached to "
                            f"“{r['subject'] or '(no subject)'}”", "date": (r["date_utc"] or "")[:10],
                            "from": r["from_addr"], "ref": r["ref"]})
            if len(out) >= MAX_ATTACHMENTS:
                return out[:MAX_ATTACHMENTS]
    return out


def _files(config, terms: set[str], allowed: list[str], role: str) -> list[dict]:
    """Files whose names match the question, in folders the owner allowed for the life story and still chose
    (names only), never the private export."""
    from towpath.photos import private_in

    if not terms or not store_path(config.store_dir, "files").exists():
        return []
    with closing(open_store(config.store_dir, "files", role)) as db:
        providers = [p for p in allowed if db.execute("SELECT 1 FROM occurrences WHERE provider_id = ? LIMIT 1",
                                                       (p,)).fetchone()]
        if not providers:
            return []
        words = sorted(terms, key=len, reverse=True)[:4]
        match = " AND ".join('"' + w.replace('"', "") + '"' for w in words if len(w) >= 3)
        if not match:
            return []
        rows = db.execute(
            "SELECT o.provider_id, o.occurrence_id, o.root_alias, o.rel_path, o.dates FROM occurrences_text t JOIN "
            f"occurrences o ON o.rowid = t.rowid WHERE t.text MATCH ? AND o.missing_since_run IS NULL AND "
            f"o.provider_id IN ({','.join('?' for _ in providers)}) LIMIT 200", (match, *providers)).fetchall()
    out = []
    for provider, occ, root, rel, dates in rows:
        if private_in(config, root, rel):
            continue
        when = next((d.get("value") for d in json.loads(dates or "[]") if d.get("meaning") == "file-modified"), "")
        out.append({"kind": "file", "text": f"{root}/{rel}", "date": (when or "")[:10], "ref": f"{provider}:{occ}"})
        if len(out) >= MAX_FILES:
            break
    return out
