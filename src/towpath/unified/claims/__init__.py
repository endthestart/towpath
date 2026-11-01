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

from towpath.unified.claims.records import Claim, timeline, validate
from towpath.unified.claims.values import Citation, ClaimError, DateBasis, When

__all__ = [
    "Citation", "Claim", "ClaimError", "DateBasis", "When", "timeline", "validate",
]
