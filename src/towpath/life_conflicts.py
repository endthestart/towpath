"""Life events that may disagree: two events worded nearly alike that are on about the same day (likely one event
found twice, perhaps from two sources) or within a few months of each other (perhaps one event with dates that
disagree). The owner settles each pair: keep one and reject the other, or say both are right, which is remembered.
"""

import json
import re
from contextlib import closing
from datetime import date

from towpath import life
from towpath.stores import open_store, store_path

SAME_DAYS = 3
APART_DAYS = 120
SIMILAR = 0.75
TARGET = "towpath:life-pairs"
STOP = {"the", "and", "for", "with", "from", "into", "onto", "about", "was", "were", "has", "had", "have", "his", "her",
        "their", "our", "your", "you", "she", "they", "them", "that", "this", "then", "than", "who", "what", "when"}


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9']+", text.lower()) if len(w) > 2 and w not in STOP}


def _day(value: str | None) -> date | None:
    try:
        return date.fromisoformat((value or "")[:10])
    except ValueError:
        return None


def _key(a: str, b: str) -> str:
    return "|".join(sorted((a, b)))


def settled(store_dir, role: str = "web") -> set[str]:
    if not store_path(store_dir, "decisions").exists():
        return set()
    with closing(open_store(store_dir, "decisions", role)) as db:
        return {r[0] for r in db.execute("SELECT key FROM settings WHERE target_id = ?", (TARGET,))}


def both_right(store_dir, a: str, b: str, author: str = "owner") -> None:
    from towpath.decisions import now

    with closing(open_store(store_dir, "decisions", "web")) as db:
        db.execute("INSERT OR REPLACE INTO settings (target_id, key, value, author, at) VALUES (?,?,?,?,?)",
                   (TARGET, _key(a, b), json.dumps("both-right"), author, now()))
        db.execute("INSERT INTO decision_log (kind, target_id, detail, author, at) VALUES ('life-pair', ?, ?, ?, ?)",
                   (TARGET, _key(a, b), author, now()))
        db.commit()


def find(store_dir, role: str = "web", limit: int = 100) -> list[dict]:
    """Pairs to settle, most alike first."""
    claims = [c for c in life.claims(store_dir, limit=10**6, role=role)
              if c.state != "rejected" and _day(c.when.earliest) and c.when.precision in ("day", "month")]
    done = settled(store_dir, role)
    words = {c.claim_id: _words(c.statement) for c in claims}
    index: dict[str, list] = {}
    for c in claims:
        for w in words[c.claim_id]:
            index.setdefault(w, []).append(c)
    seen, out = set(), []
    for c in claims:
        for other in {o.claim_id: o for w in words[c.claim_id] for o in index[w]}.values():
            key = _key(c.claim_id, other.claim_id)
            if other.claim_id == c.claim_id or key in seen or key in done or c.modality != other.modality:
                continue
            seen.add(key)
            a, b = words[c.claim_id], words[other.claim_id]
            if len(a & b) < 2:
                continue
            alike = len(a & b) / len(a | b)
            apart = abs((_day(c.when.earliest) - _day(other.when.earliest)).days)
            if alike < SIMILAR or apart > APART_DAYS:
                continue
            first, second = sorted((c, other), key=lambda x: (x.when.earliest or "", x.claim_id))
            out.append({"key": key, "alike": round(alike, 2), "apart": apart, "a": first, "b": second,
                        "kind": "the same event twice?" if apart <= SAME_DAYS else "one event, dates that disagree?"})
    out.sort(key=lambda p: (-p["alike"], p["apart"]))
    return out[:limit]
