"""A claim's vocabulary, and the parts it is made of: uncertain dates and citations."""

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from towpath.unified.contracts import DATE_MEANINGS, ContractError, Reference

SCHEMA = "towpath.claim/0"
KINDS = ("event", "activity", "relationship", "state", "accomplishment")
MODALITIES = ("plan", "occurred", "recollected", "inferred")
PRECISIONS = ("instant", "day", "month", "year", "range", "unknown")
AUDIENCES = ("owner", "shareable")
MODEL_USE = ("follow-grants", "local-only", "excluded")
STATES = ("proposed", "accepted", "rejected", "corrected", "superseded")
RELATIONS = ("contradicts", "supports", "same-event", "corrects", "supersedes")
SIGNIFICANCE = ("milestone", "notable", "routine")  # a producer's judgement; unknown when absent
CLAIM_KEYS = {"schema", "claim_id", "kind", "statement", "modality", "when", "citations", "audience", "model_use",
              "producer", "state", "uncertainty", "author", "review", "relations", "subjects", "significance"}
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
