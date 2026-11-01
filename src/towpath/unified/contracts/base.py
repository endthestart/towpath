"""The contracts' shared vocabulary, source IDs, references to items, and dates that carry their meaning."""

import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from urllib.parse import quote, unquote

SEARCH_SCHEMA = "towpath.search/0"
RESULT_SCHEMA = "towpath.result/0"
SOURCE_SCHEMA = "towpath.source-status/0"
CITATION_SCHEMA = "towpath.citation/0"

SEARCH_DEPTHS = ("catalog", "content-index", "provider-search")
SOURCE_TYPES = ("gmail", "imap", "files", "manifest")
SOURCE_STATES = ("ready", "not-indexed", "unavailable", "disabled", "not-configured")
CAPABILITY_STATES = ("verified", "unverified", "not-implemented", "disabled")
CAPABILITIES = ("catalog-search", "content-search", "provider-search", "describe", "selected-content",
                "excerpt", "incremental")
COVERAGE_STATES = ("discovered", "metadata-cataloged", "text-extracted", "pending", "unreadable", "unsupported",
                   "excluded", "failed", "unknown")
PAGE_STATUSES = ("ok", "partial", "unavailable", "denied", "error", "not-supported", "unknown-source")
RESULT_KINDS = ("mail-message", "mail-part", "file", "archive-member", "manifest-entry")
AVAILABILITY = ("present", "absent", "missing", "unknown")

# Dates about the thing a source holds, versus dates about a process that copied or indexed it.
EVIDENCE_DATES = ("message-date", "provider-received", "file-modified", "member-modified", "document-modified",
                  "captured")
PROCESS_DATES = ("indexed-at", "observed-at", "exported-at", "recovered-at", "imported-at")
DATE_MEANINGS = EVIDENCE_DATES + PROCESS_DATES
PRECISIONS = ("instant", "day", "month", "year", "range", "unknown")

MAX_CURSOR_BYTES = 4096
SOURCE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


class ContractError(ValueError):
    """A value does not satisfy a unified contract."""


def _one_of(value, allowed, what: str):
    if value not in allowed:
        raise ContractError(f"unknown {what} {value!r}; expected one of {', '.join(allowed)}")
    return value


def check_source_id(source_id: str) -> str:
    if not isinstance(source_id, str) or not SOURCE_ID.match(source_id):
        raise ContractError(f"source id {source_id!r} must be letters, digits, '.', '-' or '_' (no ':')")
    return source_id


@dataclass(frozen=True)
class Reference:
    """A stable pointer to one thing in one source: ``<source_id>:<native>``.

    ``native`` is the source's own identity, never a Towpath guess: a Gmail message ID, an IMAP
    ``mailbox;UIDVALIDITY=v;UID=u`` triple (see :func:`imap_native`), or a file occurrence ID.
    A reference names a place; the result's ``version`` says which state of it was seen.
    """

    source_id: str
    native: str

    def __post_init__(self):
        check_source_id(self.source_id)
        if not self.native or "\x00" in self.native or len(self.native) > 1024:
            raise ContractError("a reference needs a native identity (at most 1024 characters, no NUL)")

    def __str__(self) -> str:
        return f"{self.source_id}:{self.native}"

    @classmethod
    def parse(cls, text: str) -> "Reference":
        source_id, sep, native = (text or "").partition(":")
        if not sep:
            raise ContractError(f"reference {text!r} is not <source>:<native>")
        return cls(source_id, native)


def imap_native(mailbox: str, uidvalidity: int, uid: int) -> str:
    """IMAP identity per RFC 9051 section 2.3.1.1: mailbox, UIDVALIDITY and UID together."""
    if int(uidvalidity) < 1 or int(uid) < 1:
        raise ContractError("UIDVALIDITY and UID are positive integers")
    return f"{quote(mailbox, safe='/')};UIDVALIDITY={int(uidvalidity)};UID={int(uid)}"


def parse_imap_native(native: str) -> tuple[str, int, int]:
    match = re.fullmatch(r"([^;]+);UIDVALIDITY=([1-9][0-9]*);UID=([1-9][0-9]*)", native or "")
    if not match:
        raise ContractError(f"{native!r} is not an IMAP mailbox;UIDVALIDITY=v;UID=u identity")
    return unquote(match.group(1)), int(match.group(2)), int(match.group(3))


def part_native(native: str, part_id: str) -> str:
    """A MIME part inside a message: ``<message native>#part=<part id>``."""
    if not re.fullmatch(r"[0-9A-Za-z.]{1,64}", part_id or ""):
        raise ContractError(f"part id {part_id!r} is not a MIME part identifier")
    return f"{native}#part={part_id}"


def split_part(native: str) -> tuple[str, str | None]:
    """(message native, part id or None)."""
    message, sep, part = native.rpartition("#part=")
    return (message, part) if sep else (native, None)


def _parse_iso(value: str) -> datetime | date:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")) if "T" in value else date.fromisoformat(value)
    except (AttributeError, ValueError):
        raise ContractError(f"date {value!r} is not ISO 8601") from None


@dataclass(frozen=True)
class TypedDate:
    """A date with what it means, how precise it is, and where it came from."""

    meaning: str
    value: str
    precision: str = "instant"
    basis: str | None = None

    def __post_init__(self):
        _one_of(self.meaning, DATE_MEANINGS, "date meaning")
        _one_of(self.precision, PRECISIONS, "date precision")
        if self.precision != "unknown":
            _parse_iso(self.value)

    @property
    def is_process_date(self) -> bool:
        return self.meaning in PROCESS_DATES

    def to_dict(self) -> dict:
        return {"meaning": self.meaning, "value": self.value, "precision": self.precision, "basis": self.basis,
                "process_date": self.is_process_date}

    @classmethod
    def from_dict(cls, data: dict) -> "TypedDate":
        return cls(data["meaning"], data["value"], data.get("precision", "instant"), data.get("basis"))

    def day(self) -> str | None:
        """The calendar day (UTC for instants), for date filters; None when unknown."""
        if self.precision == "unknown":
            return None
        parsed = _parse_iso(self.value)
        if isinstance(parsed, datetime):
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone(timezone.utc)
            return parsed.date().isoformat()
        return parsed.isoformat()
