"""One search result, with its matches, citations and excerpts."""

import re
from dataclasses import dataclass, field

from towpath.unified.contracts.base import (
    AVAILABILITY,
    CITATION_SCHEMA,
    COVERAGE_STATES,
    RESULT_KINDS,
    RESULT_SCHEMA,
    SEARCH_DEPTHS,
    SOURCE_TYPES,
    ContractError,
    Reference,
    TypedDate,
    _one_of,
    check_source_id,
)

@dataclass(frozen=True)
class Match:
    """Why a result is here. ``fields`` names what matched (subject, body, name, text, ...)."""

    depth: str
    fields: tuple[str, ...] = ()
    verified_passage: bool = False
    provider_rank: int | None = None

    def __post_init__(self):
        _one_of(self.depth, SEARCH_DEPTHS, "search depth")
        if self.depth == "provider-search" and self.verified_passage:
            raise ContractError("a provider-search match does not supply a verified passage")

    def to_dict(self) -> dict:
        return {"depth": self.depth, "fields": list(self.fields), "verified_passage": self.verified_passage,
                "provider_rank": self.provider_rank}


@dataclass(frozen=True)
class Citation:
    """A pointer to exact evidence: which reference, which version, where inside it, and a text hash."""

    ref: str
    version: str | None
    location: dict
    excerpt_sha256: str | None = None
    citation_id: str | None = None
    source_stamp: dict | None = None
    created_at: str | None = None

    def __post_init__(self):
        Reference.parse(self.ref)
        if not isinstance(self.location, dict) or "kind" not in self.location:
            raise ContractError("a citation location needs a kind (text-offset, mime-part, record, ...)")
        if self.excerpt_sha256 is not None and not re.fullmatch(r"[0-9a-f]{64}", self.excerpt_sha256):
            raise ContractError("excerpt_sha256 must be a lowercase hex SHA-256")

    def to_dict(self) -> dict:
        return {"schema": CITATION_SCHEMA, "citation_id": self.citation_id, "ref": self.ref,
                "version": self.version, "location": dict(self.location), "excerpt_sha256": self.excerpt_sha256,
                "source_stamp": self.source_stamp, "created_at": self.created_at}

    @classmethod
    def from_dict(cls, data: dict) -> "Citation":
        return cls(data["ref"], data.get("version"), dict(data["location"]), data.get("excerpt_sha256"),
                   data.get("citation_id"), data.get("source_stamp"), data.get("created_at"))

    @classmethod
    def from_file_citation(cls, source_id: str, citation: dict) -> "Citation":
        """Wrap an existing ``files`` citation (towpath.discovery) without changing it."""
        return cls(str(Reference(source_id, citation["occurrence_id"])), citation.get("version"),
                   dict(citation["location"]), citation.get("excerpt_sha256"), citation.get("citation_id"),
                   citation.get("source"), citation.get("created_at"))


@dataclass(frozen=True)
class Excerpt:
    """Permitted, bounded text. Always untrusted data copied from a source, never instructions."""

    text: str
    citation: Citation
    cut_at_limit: bool
    max_bytes: int

    def __post_init__(self):
        if len(self.text.encode("utf-8")) > self.max_bytes:
            raise ContractError("excerpt text exceeds its byte budget")

    def to_dict(self) -> dict:
        return {"text": self.text, "citation": self.citation.to_dict(), "cut_at_limit": self.cut_at_limit,
                "max_bytes": self.max_bytes, "content_is_untrusted_data": True}


@dataclass(frozen=True)
class Result:
    """One search hit in the common envelope. ``legacy`` holds the original mail or file record."""

    source_id: str
    source_type: str
    ref: Reference
    kind: str
    title: str | None
    match: Match
    version: str | None = None
    dates: tuple[TypedDate, ...] = ()
    locator: dict = field(default_factory=dict)
    media_type: str | None = None
    size: int | None = None
    extension: str | None = None
    availability: str = "present"
    coverage: str = "unknown"
    restrictions: dict = field(default_factory=dict)
    hashes: dict = field(default_factory=dict)
    excerpt: Excerpt | None = None
    legacy: dict | None = None

    def __post_init__(self):
        check_source_id(self.source_id)
        _one_of(self.source_type, SOURCE_TYPES, "source type")
        _one_of(self.kind, RESULT_KINDS, "result kind")
        _one_of(self.availability, AVAILABILITY, "availability")
        _one_of(self.coverage, COVERAGE_STATES, "coverage state")
        if self.ref.source_id != self.source_id:
            raise ContractError("a result's reference must name its own source")

    def to_dict(self) -> dict:
        return {
            "schema": RESULT_SCHEMA, "source_id": self.source_id, "source_type": self.source_type,
            "ref": str(self.ref), "native": self.ref.native, "kind": self.kind, "title": self.title,
            "version": self.version, "dates": [d.to_dict() for d in self.dates], "locator": dict(self.locator),
            "media_type": self.media_type, "size": self.size, "extension": self.extension,
            "availability": self.availability, "coverage": self.coverage, "restrictions": dict(self.restrictions),
            "hashes": dict(self.hashes), "match": self.match.to_dict(),
            "excerpt": self.excerpt.to_dict() if self.excerpt else None, "legacy": self.legacy,
        }
