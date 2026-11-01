"""One claim, checking a claim's record, and building a timeline from claims."""

from dataclasses import dataclass, field

from towpath.canonical import canonical_json
from towpath.unified.claims.values import (
    AUDIENCES,
    CLAIM_ID,
    CLAIM_KEYS,
    KINDS,
    MODALITIES,
    MODEL_USE,
    RELATIONS,
    SCHEMA,
    SIGNIFICANCE,
    STATES,
    Citation,
    ClaimError,
    When,
)
from towpath.unified.contracts import PROCESS_DATES


@dataclass(frozen=True)
class Claim:
    claim_id: str
    kind: str
    statement: str
    modality: str
    when: When
    citations: tuple[Citation, ...]
    producer: str
    state: str = "proposed"
    audience: str = "owner"
    model_use: str = "follow-grants"
    uncertainty: str | None = None
    author: str | None = None
    review: dict | None = None
    relations: tuple[dict, ...] = ()
    subjects: tuple[str, ...] = field(default_factory=tuple)
    significance: str | None = None

    def __post_init__(self):
        if not CLAIM_ID.match(self.claim_id):
            raise ClaimError("claim_id looks like clm_<letters, digits, '-' or '_'>")
        for value, allowed, what in ((self.kind, KINDS, "kind"), (self.modality, MODALITIES, "modality"),
                                     (self.state, STATES, "state"), (self.audience, AUDIENCES, "audience"),
                                     (self.model_use, MODEL_USE, "model_use")):
            if value not in allowed:
                raise ClaimError(f"{what} must be one of {', '.join(allowed)}")
        if self.significance is not None and self.significance not in SIGNIFICANCE:
            raise ClaimError(f"significance must be one of {', '.join(SIGNIFICANCE)}")
        if not self.statement.strip():
            raise ClaimError("a claim needs a statement")
        if not self.citations and self.modality != "recollected":
            raise ClaimError("only a recollection may stand without a citation")
        for basis in self.when.basis:
            if not 0 <= basis.citation < len(self.citations):
                raise ClaimError("when.basis points at a citation that does not exist")
            facts = {f["meaning"] for f in self.citations[basis.citation].date_facts}
            if facts and basis.meaning not in facts:
                raise ClaimError("when.basis names a date meaning its citation does not carry")
        if self.when.basis and all(b.meaning in PROCESS_DATES for b in self.when.basis) \
                and self.modality in {"occurred", "plan"}:
            raise ClaimError("process dates (indexing, observation, export, recovery, import) are not evidence of "
                             "when something happened; cite an evidence date or use modality inferred")
        if self.state in {"accepted", "corrected"} and not (self.review and self.review.get("by")):
            raise ClaimError(f"an {self.state} claim records who reviewed it")
        for relation in self.relations:
            if relation.get("type") not in RELATIONS or not CLAIM_ID.match(str(relation.get("claim_id", ""))):
                raise ClaimError(f"relations have a type ({', '.join(RELATIONS)}) and a claim_id")
            if relation["claim_id"] == self.claim_id:
                raise ClaimError("a claim cannot relate to itself")

    def to_dict(self) -> dict:
        out = {"schema": SCHEMA, "claim_id": self.claim_id, "kind": self.kind, "statement": self.statement,
               "modality": self.modality, "when": self.when.to_dict(),
               "citations": [c.to_dict() for c in self.citations], "audience": self.audience,
               "model_use": self.model_use, "producer": self.producer, "state": self.state,
               "uncertainty": self.uncertainty, "author": self.author, "review": self.review,
               "relations": [dict(r) for r in self.relations], "subjects": list(self.subjects)}
        if self.significance is not None:
            out["significance"] = self.significance
        return out

    @classmethod
    def from_dict(cls, data: dict) -> "Claim":
        if data.get("schema") != SCHEMA:
            raise ClaimError(f"unsupported claim schema {data.get('schema')!r}; expected {SCHEMA}")
        unknown = set(data) - CLAIM_KEYS
        if unknown:
            raise ClaimError(f"unknown claim field(s): {', '.join(sorted(unknown))}")
        missing = {"claim_id", "kind", "statement", "modality", "when", "producer"} - set(data)
        if missing:
            raise ClaimError(f"missing claim field(s): {', '.join(sorted(missing))}")
        return cls(data["claim_id"], data["kind"], data["statement"], data["modality"], When.from_dict(data["when"]),
                   tuple(Citation.from_dict(c) for c in data.get("citations", [])), data["producer"],
                   data.get("state", "proposed"), data.get("audience", "owner"),
                   data.get("model_use", "follow-grants"), data.get("uncertainty"), data.get("author"),
                   data.get("review"), tuple(dict(r) for r in data.get("relations", [])),
                   tuple(data.get("subjects", [])), data.get("significance"))

    def canonical(self) -> str:
        return canonical_json(self.to_dict())


def validate(data: dict) -> list[str]:
    """Problems with one claim document; an empty list means it is valid and round-trips exactly."""
    try:
        claim = Claim.from_dict(data)
    except (ClaimError, KeyError, TypeError) as exc:
        return [str(exc) if not isinstance(exc, KeyError) else f"missing field {exc}"]
    again = Claim.from_dict(claim.to_dict())
    return [] if again == claim and again.canonical() == claim.canonical() else ["claim does not round-trip"]


def timeline(claims: list[Claim]) -> dict:
    """Claims ordered by earliest bound, with contradictions surfaced and nothing merged.

    Dated claims come first in date order (ties keep input order); undated ones follow. A pair is a
    conflict when one claim says it contradicts the other, or when two claims share a same-event
    relation and their date bounds do not overlap.
    """
    dated = sorted((c for c in claims if c.when.bounds()), key=lambda c: c.when.bounds())
    undated = [c for c in claims if not c.when.bounds()]
    by_id = {c.claim_id: c for c in claims}
    conflicts = []
    for claim in claims:
        for relation in claim.relations:
            other = by_id.get(relation["claim_id"])
            if other is None:
                continue
            pair = sorted([claim.claim_id, other.claim_id])
            if relation["type"] == "contradicts":
                conflicts.append({"claims": pair, "why": "marked as contradicting"})
            elif relation["type"] == "same-event" and claim.when.bounds() and other.when.bounds():
                (a0, a1), (b0, b1) = claim.when.bounds(), other.when.bounds()
                if a1 < b0 or b1 < a0:
                    conflicts.append({"claims": pair, "why": "same event, but the dates do not overlap"})
    unique = {tuple(c["claims"]): c for c in conflicts}
    return {"entries": [{"claim_id": c.claim_id, "modality": c.modality, "precision": c.when.precision,
                         "earliest": c.when.earliest, "latest": c.when.latest, "expression": c.when.expression,
                         "statement": c.statement, "state": c.state} for c in dated + undated],
            "conflicts": list(unique.values()),
            "notes": ["Plans, recollections and inferences are listed with their modality; none is promoted to "
                      "an occurred event.", "Conflicting claims are both kept; no winner is chosen."]}
