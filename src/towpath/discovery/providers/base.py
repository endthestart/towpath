"""The provider contract.

A provider is an existing search tool. Towpath treats everything it returns as
untrusted: the service re-checks every reference against the configured roots,
exclusions, and grants before showing anything. Any operation may raise
``Unavailable`` with a reason a person can act on.
"""

from dataclasses import dataclass, field

from towpath.discovery.records import DateFact, Extraction, Member


class Unavailable(RuntimeError):
    """The provider cannot answer (not installed, not implemented, timed out, gone)."""


class LimitExceeded(RuntimeError):
    pass


@dataclass(frozen=True)
class Hit:
    """One provider row, before Towpath has checked it."""

    native_id: str
    url: str                      # file: URL (or absolute path) of the outermost file on disk
    members: tuple[Member, ...]
    extraction: Extraction
    media_type: str | None = None
    size: int | None = None
    dates: tuple[DateFact, ...] = ()
    hashes: dict = field(default_factory=dict)
    version: str | None = None    # which state of this occurrence the provider's index holds
    passage: dict | None = None   # where the match is, e.g. {"kind": "text-offset", "start": 120}
    # The outer file as the index recorded it: {"size", "mtime", "ctime", "basis"}, each field optional.
    # Compared with the file on disk before any content is returned (see towpath.discovery.freshness).
    source_stamp: dict | None = None


@dataclass(frozen=True)
class Listing:
    """Rows from one bounded provider query, after the provider's own filtering.

    ``exhausted`` describes the provider's raw rows, not ``hits``: True only when the provider
    confirmed nothing lay beyond the rows it examined (it asked for one more than the cap and got
    none); False when the cap was reached; None when the provider cannot say. Filtering (folders,
    unusable rows) shortens ``hits`` but never makes a capped listing look exhausted.

    ``cursor``, when a provider pages an enumeration by position rather than by offset, is where the next
    page starts; the caller passes it back instead of an offset.
    """

    hits: list
    exhausted: bool | None
    raw_rows: int = 0
    cursor: str | None = None


@dataclass(frozen=True)
class Excerpt:
    text: str
    start: int                    # offset in the extracted text, in characters
    extracted_bytes: int | None   # total extracted text available, if known
    truncated_source: bool | None  # the provider's own extraction was cut short
    media_type: str = "text/plain"


# Capability values: "verified", "unverified", "not-implemented".
CAPABILITIES = ("probe", "search", "describe", "excerpt", "recover", "enumerate", "root-scoped-query")


class BaseProvider:
    adapter = "base"
    capabilities: dict = {}

    def __init__(self, provider_config, files_config):
        self.config = provider_config
        self.files = files_config
        self.id = provider_config.id
        self.roots = {alias: files_config.roots[alias] for alias in provider_config.roots}

    def probe(self, timeout: float) -> dict:
        raise Unavailable("probe is not implemented")

    def search(self, query: str, roots: list, max_rows: int, timeout: float) -> Listing:
        """``Hit`` rows in the provider's rank order from at most ``max_rows`` raw rows."""
        raise Unavailable("search is not implemented")

    def enumerate(self, root, max_rows: int, timeout: float, offset: int = 0) -> Listing:
        """Every item under ``root``, from at most ``max_rows`` raw rows after the first ``offset``. A provider
        whose listings carry a ``cursor`` also accepts ``cursor=`` in place of the offset."""
        raise Unavailable("enumerate is not implemented")

    def describe(self, native_id: str, timeout: float) -> Hit:
        raise Unavailable("describe is not implemented")

    def excerpt(self, native_id: str, start: int, max_bytes: int, timeout: float) -> Excerpt:
        raise Unavailable("excerpt is not implemented")

    def recover(self, native_id: str, dest, max_bytes: int, timeout: float) -> None:
        """Write the original bytes of ``native_id`` to ``dest`` (a new file in the recovery folder)."""
        raise Unavailable("recover is not implemented")
