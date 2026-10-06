"""Timeline claims (``towpath.claim/0``) and their typed dates: validation and lossless round trips.

A claim says something happened, was planned, was remembered or was inferred, and cites evidence.
These rules keep a later timeline honest:

- **Typed dates keep their precision.** ``when`` has an expression, earliest and latest bounds, and
  a precision; a day, month or year is never widened or narrowed silently.
- **Event dates need evidence dates.** ``when.basis`` names the date facts the dates came from. If
  every basis is a process date (indexed, observed, exported, recovered, imported), the claim is
  rejected: recovering a file in 2026 is not evidence that its work was done in 2026.
- **Contradictions stay visible.** Claims can contradict or support each other; ordering a timeline
  never merges them or picks a winner.
- **Citations point at evidence, never copy it silently.** A citation names a Towpath reference (or
  an external item) with an optional span, text hash and captured excerpt.

Nothing here stores claims, calls a model or extracts anything. See docs/interfaces.md section 4.
"""

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from towpath.canonical import canonical_json
from towpath.unified.contracts import DATE_MEANINGS, PROCESS_DATES, ContractError, Reference

SCHEMA = "towpath.claim/0"
KINDS = ("event", "activity", "relationship", "state", "accomplishment")
MODALITIES = ("plan", "occurred", "recollected", "inferred")
PRECISIONS = ("instant", "day", "month", "year", "range", "unknown")
AUDIENCES = ("owner", "shareable")
MODEL_USE = ("follow-grants", "local-only", "excluded")
STATES = ("proposed", "accepted", "rejected", "corrected", "superseded")
RELATIONS = ("contradicts", "supports", "same-event", "corrects", "supersedes")
CLAIM_KEYS = {"schema", "claim_id", "kind", "statement", "modality", "when", "citations", "audience", "model_use",
              "producer", "state", "uncertainty", "author", "review", "relations", "subjects"}
CLAIM_ID = re.compile(r"^clm_[A-Za-z0-9_-]{1,64}$")


class ClaimError(ValueError):
    pass


def _parse(value: str, what: str) -> date | datetime:
    try:
        if "T" in value:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        return date.fromisoformat(value)
    except (AttributeError, ValueError):
        raise ClaimError(f"{what} {value!r} is not an ISO 8601 date") from None


def _day(value: date | datetime) -> date:
    return value.date() if isinstance(value, datetime) else value


@dataclass(frozen=True)
class DateBasis:
    """Which cited date fact a claim's dates rest on."""

    citation: int
    meaning: str

    def __post_init__(self):
        if self.meaning not in DATE_MEANINGS:
            raise ClaimError(f"unknown date meaning {self.meaning!r}")


@dataclass(frozen=True)
class When:
    expression: str
    precision: str
    earliest: str | None = None
    latest: str | None = None
    basis: tuple[DateBasis, ...] = ()

    def __post_init__(self):
        if self.precision not in PRECISIONS:
            raise ClaimError(f"precision must be one of {', '.join(PRECISIONS)}")
        if not self.expression.strip():
            raise ClaimError("when.expression keeps the date as it was stated; it cannot be empty")
        if self.precision == "unknown":
            if self.earliest or self.latest:
                raise ClaimError("an unknown date has no bounds")
            return
        if not self.earliest or not self.latest:
            raise ClaimError(f"a {self.precision} date needs earliest and latest bounds")
        low, high = _parse(self.earliest, "earliest"), _parse(self.latest, "latest")
        if self.precision == "instant":
            if not isinstance(low, datetime) or low != high:
                raise ClaimError("an instant has identical earliest and latest timestamps")
            return
        if isinstance(low, datetime) or isinstance(high, datetime):
            raise ClaimError(f"a {self.precision} date is bounded by calendar days, not timestamps")
        if low > high:
            raise ClaimError("earliest is after latest")
        if self.precision == "day" and low != high:
            raise ClaimError("a day has the same earliest and latest day")
        if self.precision == "month":
            last = (low.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
            if low.day != 1 or high != last:
                raise ClaimError("a month runs from its first to its last day")
        if self.precision == "year" and (low != date(low.year, 1, 1) or high != date(low.year, 12, 31)):
            raise ClaimError("a year runs from January 1 to December 31")
        if self.precision == "range" and low == high:
            raise ClaimError("a range spans more than one day; use precision day")

    def to_dict(self) -> dict:
        return {"expression": self.expression, "earliest": self.earliest, "latest": self.latest,
                "precision": self.precision, "basis": [{"citation": b.citation, "meaning": b.meaning}
                                                       for b in self.basis]}

    @classmethod
    def from_dict(cls, data: dict) -> "When":
        unknown = set(data) - {"expression", "earliest", "latest", "precision", "basis"}
        if unknown:
            raise ClaimError(f"unknown when field(s): {', '.join(sorted(unknown))}")
        return cls(data["expression"], data["precision"], data.get("earliest"), data.get("latest"),
                   tuple(DateBasis(b["citation"], b["meaning"]) for b in data.get("basis", [])))

    def bounds(self) -> tuple[date, date] | None:
        if self.precision == "unknown":
            return None
        return _day(_parse(self.earliest, "earliest")), _day(_parse(self.latest, "latest"))


@dataclass(frozen=True)
class Citation:
    """Evidence for a claim: a Towpath reference with an optional span, or an external item."""

    ref: str | None = None
    span: tuple[int, int] | None = None
    excerpt_sha256: str | None = None
    captured_excerpt: str | None = None
    version: str | None = None
    external: dict | None = None
    observed: str | None = None
    date_facts: tuple[dict, ...] = ()
    occurrence_id: str | None = None  # the version 0 draft's mail occurrence form (docs/interfaces.md section 4)
    part: str | None = None
    preserved: dict | None = None

    def __post_init__(self):
        if sum(x is not None for x in (self.ref, self.external, self.occurrence_id)) != 1:
            raise ClaimError("a citation names exactly one of: a Towpath reference, an occurrence, an external item")
        if self.ref is not None:
            try:
                Reference.parse(self.ref)
            except ContractError as exc:
                raise ClaimError(str(exc)) from None
        if self.external is not None and not {"source_id", "native_id"} <= set(self.external):
            raise ClaimError("an external citation needs source_id and native_id")
        if self.span is not None and not (len(self.span) == 2 and 0 <= self.span[0] <= self.span[1]):
            raise ClaimError("span is [start, end] with start <= end")
        if self.excerpt_sha256 is not None and not re.fullmatch(r"[0-9a-f]{64}", self.excerpt_sha256):
            raise ClaimError("excerpt_sha256 must be a lowercase hex SHA-256")
        for fact in self.date_facts:
            if fact.get("meaning") not in DATE_MEANINGS or not fact.get("value"):
                raise ClaimError("each date fact has a known meaning and a value")

    def to_dict(self) -> dict:
        out = {k: v for k, v in (("ref", self.ref), ("occurrence_id", self.occurrence_id), ("part", self.part),
                                 ("span", list(self.span) if self.span else None),
                                 ("excerpt_sha256", self.excerpt_sha256), ("captured_excerpt", self.captured_excerpt),
                                 ("version", self.version), ("preserved", self.preserved),
                                 ("external", self.external), ("observed", self.observed)) if v is not None}
        if self.date_facts:
            out["date_facts"] = [dict(f) for f in self.date_facts]
        return out

    @classmethod
    def from_dict(cls, data: dict) -> "Citation":
        unknown = set(data) - {"ref", "span", "excerpt_sha256", "captured_excerpt", "version", "external", "observed",
                               "date_facts", "occurrence_id", "part", "preserved"}
        if unknown:
            raise ClaimError(f"unknown citation field(s): {', '.join(sorted(unknown))}")
        span = data.get("span")
        return cls(data.get("ref"), tuple(span) if span is not None else None, data.get("excerpt_sha256"),
                   data.get("captured_excerpt"), data.get("version"), data.get("external"), data.get("observed"),
                   tuple(dict(f) for f in data.get("date_facts", [])), data.get("occurrence_id"), data.get("part"),
                   data.get("preserved"))


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

    def __post_init__(self):
        if not CLAIM_ID.match(self.claim_id):
            raise ClaimError("claim_id looks like clm_<letters, digits, '-' or '_'>")
        for value, allowed, what in ((self.kind, KINDS, "kind"), (self.modality, MODALITIES, "modality"),
                                     (self.state, STATES, "state"), (self.audience, AUDIENCES, "audience"),
                                     (self.model_use, MODEL_USE, "model_use")):
            if value not in allowed:
                raise ClaimError(f"{what} must be one of {', '.join(allowed)}")
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
                   tuple(data.get("subjects", [])))

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
