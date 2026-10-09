"""Records for discovered files.

Three things are kept apart:

- **Content identity**: what the bytes are. Only an algorithm-labelled hash
  that someone actually computed establishes it; none is ever invented.
- **Occurrence**: where those bytes were seen: a root alias, a relative path,
  and the chain of members inside containers (ZIP member, mailbox message,
  attachment). Identical bytes in two places are two occurrences. The
  occurrence ID is derived from that location, so it names a place, not
  immutable evidence; the ``version`` field says which state of it was seen.
- **Extraction**: what a provider's parser made of it: status, parser and
  version, how many bytes of text, the limit, and whether it was truncated.
"""

import re
from dataclasses import asdict, dataclass, field
from functools import cached_property

from towpath.canonical import canonical_json, short_id

MEMBER_KINDS = {"archive-member", "mail-message", "attachment", "embedded"}
DATE_MEANINGS = {
    "file-modified",       # filesystem mtime of the file on disk
    "member-modified",     # mtime recorded for a member inside a container
    "message-date",        # a mail message's Date header
    "document-modified",   # a date the document's own metadata states
    "indexed-at",          # when the provider indexed it
}
STATUSES = {"indexed", "truncated", "skipped", "unsupported", "encrypted", "unreadable", "failed"}
HASH_LENGTHS = {"sha256": 64, "sha1": 40, "md5": 32}
HEX = re.compile(r"^[0-9a-f]+$")


class RecordError(ValueError):
    pass


@dataclass(frozen=True)
class Member:
    kind: str
    name: str | None = None
    index: int | None = None

    def __post_init__(self):
        if self.kind not in MEMBER_KINDS:
            raise RecordError(f"unknown member kind {self.kind!r}")
        if self.name is None and self.index is None:
            raise RecordError("a member needs a name or an index")

    def label(self) -> str:
        if self.kind == "mail-message":
            return f"message {self.index}" if self.name is None else f"message {self.name}"
        return self.name if self.name is not None else f"{self.kind} {self.index}"


@dataclass(frozen=True)
class Locator:
    """Where an occurrence lives, in the root's terms, plus the provider's own reference."""

    provider_id: str
    root: str
    path: str
    members: tuple[Member, ...] = ()
    native_id: str = ""

    def __post_init__(self):
        if self.path.startswith("/") or ".." in self.path.split("/") or "\x00" in self.path:
            raise RecordError("locator path must be relative to its root, without '..'")

    @cached_property
    def occurrence_id(self) -> str:
        chain = canonical_json([[m.kind, m.name, m.index] for m in self.members])
        return short_id("occ", self.provider_id, self.root, self.path, chain)

    def display(self) -> str:
        return " > ".join([f"{self.root}:{self.path}", *(m.label() for m in self.members)])


@dataclass(frozen=True)
class DateFact:
    meaning: str
    value: str
    basis: str

    def __post_init__(self):
        if self.meaning not in DATE_MEANINGS:
            raise RecordError(f"unknown date meaning {self.meaning!r}")


@dataclass(frozen=True)
class Extraction:
    status: str
    parser: str | None = None
    parser_version: str | None = None
    extracted_bytes: int | None = None
    limit_bytes: int | None = None
    detail: str | None = None

    def __post_init__(self):
        if self.status not in STATUSES:
            raise RecordError(f"unknown extraction status {self.status!r}")

    @property
    def truncated(self) -> bool | None:
        """True or False when known; None when the provider does not say."""
        if self.status == "truncated":
            return True
        if self.extracted_bytes is None or self.limit_bytes is None:
            return None
        return self.extracted_bytes >= self.limit_bytes


@dataclass(frozen=True)
class Occurrence:
    locator: Locator
    extraction: Extraction
    media_type: str | None = None
    size: int | None = None
    dates: tuple[DateFact, ...] = ()
    hashes: dict = field(default_factory=dict)
    # Provider-stated token for this state of the occurrence (for example size and mtime of the
    # container). None means the provider gave nothing that changes when the content does.
    version: str | None = None

    def __post_init__(self):
        for algorithm, value in self.hashes.items():
            if algorithm not in HASH_LENGTHS or len(value) != HASH_LENGTHS[algorithm] or not HEX.match(value):
                raise RecordError(f"hash {algorithm} is not a lowercase hex {algorithm} digest")

    @property
    def occurrence_id(self) -> str:
        return self.locator.occurrence_id

    def to_dict(self) -> dict:
        data = asdict(self)
        data["occurrence_id"] = self.occurrence_id
        data["location"] = self.locator.display()
        data["extraction"]["truncated"] = self.extraction.truncated
        return data


@dataclass(frozen=True)
class Coverage:
    """What one provider run actually looked at. A partial or failed run cannot establish absence."""

    provider_id: str
    complete: bool
    scanned: int
    by_status: dict
    reason: str | None = None
