"""Shared contracts for sources, search requests and results, coverage, and evidence.

Rules the shapes encode:

- **Search depth is stated per source.** ``catalog`` matches only names, types, dates and other
  metadata; ``content-index`` matches text a local index extracted; ``provider-search`` asks the
  source itself (Gmail ``q``, IMAP ``SEARCH``). A provider match supplies no verified passage.
- **Each source pages and fails on its own.** One unavailable source never erases another's results,
  and a capped page or a provider estimate is never turned into an exhaustive count.
- **Coverage states are distinct.** An inventory can be complete while text extraction is not;
  neither is inferred from the other.
- **Dates carry their meaning.** Indexing, export, recovery and observation dates are process dates
  about Towpath or a copy, never evidence of when something in a life happened.
- **Legacy payloads survive.** A result keeps the original mail or file record under ``legacy``.
"""

from towpath.unified.contracts.base import (
    ContractError,
    DATE_MEANINGS,
    PROCESS_DATES,
    Reference,
    TypedDate,
    check_source_id,
    imap_native,
    parse_imap_native,
    part_native,
    split_part,
)
from towpath.unified.contracts.filters import Filters, extension_of
from towpath.unified.contracts.results import Citation, Excerpt, Match, Result
from towpath.unified.contracts.pages import (
    CoverageSummary,
    SearchResponse,
    SourceError,
    SourcePage,
    SourceStatus,
    completeness,
    decode_cursor,
    encode_cursor,
)

__all__ = [
    "Citation", "ContractError", "CoverageSummary", "DATE_MEANINGS", "Excerpt", "Filters", "Match", "PROCESS_DATES",
    "Reference", "Result", "SearchResponse", "SourceError", "SourcePage", "SourceStatus", "TypedDate",
    "check_source_id", "completeness", "decode_cursor", "encode_cursor", "extension_of", "imap_native",
    "parse_imap_native", "part_native", "split_part",
]
