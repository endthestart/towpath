"""The owner's story: only what the owner kept, in order, ready to read, print or download.

Kept memories, accepted (or corrected) events and the owner's own recollections, grouped into the decades of their
life when the profile has a birthday (else by year), each event with a numbered note naming its evidence. Proposals
the owner hasn't accepted never appear.

The family edition is the part the owner marked *shareable*: a kept memory marked shareable brings its events, and
an event (or recollection) can be marked on its own. It holds no sources, excerpts, photos, addresses or ages: only
what happened and when, by calendar decade. Only events the owner accepted (or remembered themselves) appear, even
inside a shared memory; a memory's model-written summary is left out, and so is a title that names a person, an
address or a folder (it is shown by its kind instead). Towpath shares nothing itself; the owner downloads it and
decides.
"""

from contextlib import closing
from datetime import date

from towpath import life, life_groups
from towpath.decisions import now
from towpath.stores import open_store, store_path

KEPT = ("accepted", "corrected")
SHARE_KEY, SHAREABLE = "audience", "shareable"  # an item setting (towpath.decisions.SETTINGS)


def _share_target(store_dir, kind: str, key: str) -> str:
    if kind not in ("claim", "group") or not key or ":" in key or len(key) > 64:
        raise ValueError("only an event or a memory can be shared")
    if kind == "claim":
        found = any(c.claim_id == key for c in life.claims(store_dir, limit=10**6))  # recollections included
    else:
        found = any(g["group_id"] == key for g in life_groups.groups(store_dir))
    if not found:
        raise ValueError("no such event or memory")
    return f"{kind}:{key}"


def share(store_dir, kind: str, key: str, on: bool, author: str = "owner") -> None:
    """Mark an event or memory shareable for the family edition, or take it back."""
    import json

    target = _share_target(store_dir, kind, key)
    with closing(open_store(store_dir, "decisions", "web")) as db:
        if on:
            db.execute("INSERT OR REPLACE INTO settings (target_id, key, value, author, at) VALUES (?,?,?,?,?)",
                       (target, SHARE_KEY, SHAREABLE, author, now()))
        else:
            db.execute("DELETE FROM settings WHERE target_id = ? AND key = ?", (target, SHARE_KEY))
        db.execute("INSERT INTO decision_log (kind, target_id, detail, author, at) VALUES ('share', ?, ?, ?, ?)",
                   (target, json.dumps({"shareable": on}), author, now()))
        db.commit()


def shared(store_dir, role: str = "web") -> set[str]:
    if not store_path(store_dir, "decisions").exists():
        return set()
    with closing(open_store(store_dir, "decisions", role)) as db:
        return {r[0] for r in db.execute("SELECT target_id FROM settings WHERE key = ? AND value = ?",
                                         (SHARE_KEY, SHAREABLE))}


def chapter_of(year: int, born: date | None) -> str:
    if born is None:
        return str(year)
    age = year - born.year
    if age < 10:
        return "Childhood"
    if age < 20:
        return "The teenage years"
    return f"Your {age // 10 * 10}s"


def story(store_dir, role: str = "web", family: bool = False) -> dict:
    me = life_groups.profile(store_dir, role)
    born = date.fromisoformat(me["birthday"]) if me.get("birthday") and not family else None
    marked = shared(store_dir, role)
    claims_ = {c.claim_id: c for c in life.claims(store_dir, limit=10**6, role=role)}
    kept_claims = {k: c for k, c in claims_.items() if c.state in KEPT and
                   (not family or f"claim:{k}" in marked)}
    memories = [g for g in life_groups.groups(store_dir, role) if g["state"] == "kept" and g["kind"] != "chapter" and
                (not family or f"group:{g['group_id']}" in marked)]
    used = set()
    entries = []
    for g in memories:
        members = [claims_[m] for m in g["members"] if m in claims_ and
                   (claims_[m].state in KEPT if family else claims_[m].state != "rejected")]
        if not members:
            continue
        used.update(c.claim_id for c in members)
        title, summary = g["title"], g.get("summary")
        if family:
            summary = None  # written by a model, not the owner
            if g["kind"] in ("person", "trip") or any(ch in title for ch in "@/\\"):
                title = g["label"]
        entries.append({"date": g["earliest"] or "", "title": title, "summary": summary,
                        "events": members, "kind": g["label"], "group_id": g["group_id"],
                        "shareable": f"group:{g['group_id']}" in marked})
    for c in kept_claims.values():
        if c.claim_id not in used:
            entries.append({"date": c.when.earliest or "", "title": None, "summary": None, "events": [c],
                            "kind": "Memory" if c.modality == "recollected" else "Event"})
    entries.sort(key=lambda e: e["date"])
    notes, chapters = [], {}
    for e in entries:
        year = int(e["date"][:4]) if e["date"][:4].isdigit() else None
        name = (f"The {year // 10 * 10}s" if family else chapter_of(year, born)) if year else "Undated"
        lines = []
        for c in e["events"]:
            if family:  # what happened and when, nothing that points into the owner's records
                lines.append({"statement": c.statement, "when": c.when.expression, "note": None,
                              "remembered": c.modality == "recollected", "photo": None, "claim_id": c.claim_id})
                continue
            n = None
            if c.citations:
                notes.append({"n": len(notes) + 1, "ref": c.citations[0].ref, "quote": c.citations[0].captured_excerpt,
                              "claim_id": c.claim_id})
                n = len(notes)
            photo = next((cit.ref.partition(":")[2] for cit in c.citations if cit.ref.startswith("folders:")), None)
            lines.append({"statement": c.statement, "when": c.when.expression, "note": n,
                          "remembered": c.modality == "recollected", "photo": photo, "claim_id": c.claim_id,
                          "shareable": f"claim:{c.claim_id}" in marked})
        chapters.setdefault(name, []).append({**e, "lines": lines, "year": year})
    return {"name": me.get("name"), "chapters": list(chapters.items()), "notes": notes, "family": family}


def markdown(found: dict) -> str:
    out = [f"# {found['name'] + chr(39) + 's' if found['name'] else 'My'} story", ""]
    if found.get("family"):
        out += ["The family edition: what I chose to share.", ""]
    for name, entries in found["chapters"]:
        out += [f"## {name}", ""]
        for e in entries:
            if e["title"]:
                out += [f"### {e['title']}" + (f" ({e['year']})" if e["year"] else ""), ""]
                if e["summary"]:
                    out += [e["summary"], ""]
            for line in e["lines"]:
                mark = f"[^{line['note']}]" if line["note"] else (" (remembered)" if line["remembered"] else "")
                out.append(f"- {line['statement']}, {line['when']}{mark}")
            out.append("")
    if found["notes"]:
        out += ["## Sources", ""]
        out += [f"[^{n['n']}]: {n['ref']}" + (f" — “{n['quote']}”" if n["quote"] else "") for n in found["notes"]]
    return "\n".join(out).rstrip() + "\n"
