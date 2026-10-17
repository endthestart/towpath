"""The life story's foundation: claims proposed from evidence, and the owner's review of them.

A proposal run reads a slice of one source the owner chose and stores ``towpath.claim/0`` documents in the life
store (rebuildable, written by the worker). The owner's decisions (accept, reject, or correct a claim) live in the
decisions store, keyed by claim ID, so they survive re-running a proposal and changing the model. What a page shows
is the proposed claim with the owner's latest decision applied (``reviewed``).

Claim IDs come from what a claim says and cites, so proposing the same claim again keeps its ID and its review.
"""

import json
from datetime import datetime, timezone

from towpath.canonical import digest
from towpath.stores import open_store, store_path
from towpath.unified.claims import Claim, ClaimError, When

WRITER = "worker"
REVIEW_KEY = "review"
REVIEWS = ("accept", "reject", "correct", "reopen")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def claim_id(statement: str, modality: str, refs: list[str]) -> str:
    """A stable ID for what a claim says and cites."""
    return "clm_" + digest({"statement": " ".join(statement.split()).lower(), "modality": modality,
                             "refs": sorted(refs)})[:24]


def save(db, claim: Claim, source_id: str | None, run_id: str) -> bool:
    """Store a proposed claim; False when an identical claim is already stored."""
    bounds = claim.when.bounds()
    cur = db.execute("INSERT OR IGNORE INTO claims (claim_id, body, earliest, latest, source_id, run_id, created_at) "
                     "VALUES (?,?,?,?,?,?,?)",
                     (claim.claim_id, claim.canonical(), bounds[0].isoformat() if bounds else None,
                      bounds[1].isoformat() if bounds else None, source_id, run_id, now()))
    return cur.rowcount == 1


def load(db, claim_id_: str) -> Claim | None:
    row = db.execute("SELECT body FROM claims WHERE claim_id = ?", (claim_id_,)).fetchone()
    return Claim.from_dict(json.loads(row[0])) if row else None


RECOLLECTION_KEY = "recollection"


def recollect(store_dir, statement: str, expression: str, precision: str, earliest: str, latest: str,
              people: list[str] | None = None, author: str = "owner") -> Claim:
    """The owner's own memory, kept in their words (decisions store): accepted as theirs, with no citation."""
    import uuid

    statement = " ".join(statement.split())
    if not statement:
        raise ValueError("write down the memory")
    when = When(expression or earliest, precision, earliest or None, latest or earliest or None)
    when.bounds()
    claim = Claim("clm_rec_" + uuid.uuid4().hex[:16], "event", statement[:500], "recollected", when, (), "owner",
                  state="accepted", author=author, review={"by": author, "at": now()},
                  subjects=tuple(p.strip() for p in (people or []) if p.strip()))
    db = open_store(store_dir, "decisions", "web")
    try:
        db.execute("INSERT INTO settings (target_id, key, value, author, at) VALUES (?,?,?,?,?)",
                   (_target(claim.claim_id), RECOLLECTION_KEY, claim.canonical(), author, now()))
        db.commit()
    finally:
        db.close()
    return claim


def recollections(store_dir, role: str = "web") -> list[Claim]:
    if not store_path(store_dir, "decisions").exists():
        return []
    db = open_store(store_dir, "decisions", role)
    try:
        rows = db.execute("SELECT value FROM settings WHERE key = ?", (RECOLLECTION_KEY,)).fetchall()
    finally:
        db.close()
    out = []
    for (value,) in rows:
        try:
            out.append(Claim.from_dict(json.loads(value)))
        except (ClaimError, ValueError, KeyError):
            continue
    return out


def forget(store_dir, claim_id_: str) -> bool:
    """Remove one of the owner's recollections."""
    db = open_store(store_dir, "decisions", "web")
    try:
        n = db.execute("DELETE FROM settings WHERE target_id = ? AND key = ?",
                       (_target(claim_id_), RECOLLECTION_KEY)).rowcount
        db.commit()
    finally:
        db.close()
    return n > 0


def claims(store_dir, after: str | None = None, before: str | None = None, limit: int = 200,
           role: str = "web") -> list[Claim]:
    """Proposed claims in date order (undated last), each with the owner's latest review applied, and the owner's
    own recollections."""
    remembered = [c for c in recollections(store_dir, role)
                  if (not after or (c.when.latest or "9999") >= after) and
                  (not before or (c.when.earliest or "") <= before)]
    if not store_path(store_dir, "life").exists():
        return remembered[:limit]
    db = open_store(store_dir, "life", role)
    try:
        where, args = [], []
        if after:
            where.append("(latest IS NULL OR latest >= ?)")
            args.append(after)
        if before:
            where.append("(earliest IS NULL OR earliest <= ?)")
            args.append(before)
        rows = db.execute("SELECT body FROM claims" + (f" WHERE {' AND '.join(where)}" if where else "") +
                          " ORDER BY earliest IS NULL, earliest, claim_id LIMIT ?", (*args, limit)).fetchall()
    finally:
        db.close()
    proposed = [Claim.from_dict(json.loads(r[0])) for r in rows]
    found = reviews(store_dir, [c.claim_id for c in proposed], role)
    merged = [reviewed(c, found.get(c.claim_id)) for c in proposed] + remembered
    merged.sort(key=lambda c: (c.when.earliest is None, c.when.earliest or "", c.claim_id))
    return merged[:limit]


# -- the owner's review (decisions store, written by the web service) -------------------------------------------


def _target(claim_id_: str) -> str:
    return f"claim:{claim_id_}"


def reviews(store_dir, claim_ids: list[str], role: str = "web") -> dict[str, dict]:
    if not claim_ids or not store_path(store_dir, "decisions").exists():
        return {}
    db = open_store(store_dir, "decisions", role)
    try:
        out = {}
        for start in range(0, len(claim_ids), 500):
            chunk = claim_ids[start:start + 500]
            marks = ",".join("?" * len(chunk))
            for target, value in db.execute(
                    f"SELECT target_id, value FROM settings WHERE key = ? AND target_id IN ({marks})",
                    (REVIEW_KEY, *(_target(c) for c in chunk))):
                out[target.split(":", 1)[1]] = json.loads(value)
        return out
    finally:
        db.close()


def review(store_dir, claim_id_: str, action: str, author: str = "owner", correction: dict | None = None) -> dict:
    """Record the owner's decision on a claim: accept, reject, correct (statement and/or dates), or reopen."""
    if action not in REVIEWS:
        raise ValueError(f"review must be one of {', '.join(REVIEWS)}")
    decision = {"state": {"accept": "accepted", "reject": "rejected", "correct": "corrected",
                          "reopen": "proposed"}[action], "by": author, "at": now()}
    if action == "correct":
        correction = {k: v for k, v in (correction or {}).items() if k in ("statement", "when") and v}
        if not correction:
            raise ValueError("a correction changes the statement or the date")
        if "when" in correction:  # refuse a malformed date before anything is stored
            try:
                When.from_dict(correction["when"]).bounds()
            except (ClaimError, KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"a corrected date needs an expression, a precision and its bounds ({exc})") from None
        decision["correction"] = correction
    db = open_store(store_dir, "decisions", "web")
    try:
        db.execute("INSERT OR REPLACE INTO settings (target_id, key, value, author, at) VALUES (?,?,?,?,?)",
                   (_target(claim_id_), REVIEW_KEY, json.dumps(decision, sort_keys=True), author, decision["at"]))
        db.execute("INSERT INTO decision_log (kind, target_id, detail, author, at) VALUES ('claim-review', ?, ?, ?, ?)",
                   (_target(claim_id_), json.dumps(decision, sort_keys=True), author, decision["at"]))
        db.commit()
    finally:
        db.close()
    return decision


def reviewed(claim: Claim, decision: dict | None) -> Claim:
    """The claim as the owner left it: their state, and their corrected statement or dates over the proposal's."""
    if not decision or decision["state"] == "proposed":
        return claim
    data = claim.to_dict()
    for key, value in (decision.get("correction") or {}).items():
        data[key] = {**value, "basis": data["when"].get("basis", [])} if key == "when" else value
    data["state"] = decision["state"]
    data["review"] = {"by": decision["by"], "at": decision["at"]}
    try:
        return Claim.from_dict(data)
    except ClaimError:
        return claim  # a correction the current rules refuse leaves the proposal as it was
