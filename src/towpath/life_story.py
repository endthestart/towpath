"""The owner's story: only what the owner kept, in order, ready to read, print or download.

Kept memories, accepted (or corrected) events and the owner's own recollections, grouped into the decades of their
life when the profile has a birthday (else by year), each event with a numbered note naming its evidence. Proposals
the owner hasn't accepted never appear. A first step toward the family edition; nothing is shared from here.
"""

from datetime import date

from towpath import life, life_groups

KEPT = ("accepted", "corrected")


def chapter_of(year: int, born: date | None) -> str:
    if born is None:
        return str(year)
    age = year - born.year
    if age < 10:
        return "Childhood"
    if age < 20:
        return "The teenage years"
    return f"Your {age // 10 * 10}s"


def story(store_dir, role: str = "web") -> dict:
    me = life_groups.profile(store_dir, role)
    born = date.fromisoformat(me["birthday"]) if me.get("birthday") else None
    claims_ = {c.claim_id: c for c in life.claims(store_dir, limit=10**6, role=role)}
    kept_claims = {k: c for k, c in claims_.items() if c.state in KEPT}
    memories = [g for g in life_groups.groups(store_dir, role) if g["state"] == "kept" and g["kind"] != "chapter"]
    used = set()
    entries = []
    for g in memories:
        members = [claims_[m] for m in g["members"] if m in claims_ and claims_[m].state != "rejected"]
        if not members:
            continue
        used.update(c.claim_id for c in members)
        entries.append({"date": g["earliest"] or "", "title": g["title"], "summary": g.get("summary"),
                        "events": members, "kind": g["label"]})
    for c in kept_claims.values():
        if c.claim_id not in used:
            entries.append({"date": c.when.earliest or "", "title": None, "summary": None, "events": [c],
                            "kind": "Memory" if c.modality == "recollected" else "Event"})
    entries.sort(key=lambda e: e["date"])
    notes, chapters = [], {}
    for e in entries:
        year = int(e["date"][:4]) if e["date"][:4].isdigit() else None
        name = chapter_of(year, born) if year else "Undated"
        lines = []
        for c in e["events"]:
            n = None
            if c.citations:
                notes.append({"n": len(notes) + 1, "ref": c.citations[0].ref, "quote": c.citations[0].captured_excerpt})
                n = len(notes)
            photo = next((cit.ref.partition(":")[2] for cit in c.citations if cit.ref.startswith("folders:")), None)
            lines.append({"statement": c.statement, "when": c.when.expression, "note": n,
                          "remembered": c.modality == "recollected", "photo": photo})
        chapters.setdefault(name, []).append({**e, "lines": lines, "year": year})
    return {"name": me.get("name"), "chapters": list(chapters.items()), "notes": notes}


def markdown(found: dict) -> str:
    out = [f"# {found['name'] + chr(39) + 's' if found['name'] else 'My'} story", ""]
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
