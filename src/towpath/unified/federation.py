"""Federated search across source adapters, one page per source, each failing on its own.

An adapter wraps one existing source (a Gmail or IMAP index, a files provider, a manifest) and
answers in the shared contracts. This module never ranks across sources, never sums estimates, and
turns any adapter failure into that source's error entry instead of failing the whole search.
"""

from datetime import datetime, timezone

from towpath.unified.contracts import (
    ContractError,
    Filters,
    Reference,
    SearchResponse,
    SourceError,
    SourcePage,
    SourceStatus,
    decode_cursor,
)

MAX_LIMIT = 100


class SourceUnavailable(RuntimeError):
    """The source cannot answer now (not configured, unreachable, not yet indexed, timed out)."""

    code = "unavailable"


class SourceDenied(PermissionError):
    """No grant covers this source, feature, or reference."""

    code = "denied"


class NotSupported(RuntimeError):
    """The source has no way to answer this kind of request (for example keyword text on a catalog)."""

    code = "not-supported"


class UnknownReference(LookupError):
    code = "not-found"


class StaleReference(RuntimeError):
    """The reference now points at a different version or identity than the one recorded."""

    code = "stale"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class SourceAdapter:
    """One source behind the shared contracts. Subclasses implement what their source supports."""

    source_id: str = ""
    source_type: str = ""

    def status(self) -> SourceStatus:
        raise NotImplementedError

    def search(self, filters: Filters, cursor: str | None, limit: int) -> SourcePage:
        raise NotSupported("search is not implemented for this source")

    def describe(self, native: str) -> dict:
        raise NotSupported("describe is not implemented for this source")

    def close(self) -> None:
        pass


def error_page(source_id: str, exc: BaseException) -> SourcePage:
    """A failed source as data. Unexpected errors report their type only (messages can hold mail data)."""
    from towpath.adapters.errors import AuthStop, PermissionStop, SyncStop

    if isinstance(exc, (AuthStop, PermissionStop)):
        return SourcePage(source_id, "denied", error=SourceError(exc.code, exc.reason[:300]))
    if isinstance(exc, SyncStop):
        return SourcePage(source_id, "unavailable", error=SourceError(exc.code, exc.reason[:300], True))
    if isinstance(exc, (StaleReference, UnknownReference)):
        return SourcePage(source_id, "error", error=SourceError(exc.code, str(exc)[:300]))
    if isinstance(exc, (SourceDenied, SourceUnavailable, NotSupported, ContractError)):
        status = {SourceDenied: "denied", SourceUnavailable: "unavailable", NotSupported: "not-supported"}.get(
            type(exc), "error")
        code = "invalid-request" if isinstance(exc, ContractError) else exc.code
        return SourcePage(source_id, status, error=SourceError(code, str(exc)[:300], status == "unavailable"))
    try:  # file discovery is optional; its errors exist only when it is installed and used
        from towpath.discovery import policy
        from towpath.discovery import service as files_service
        from towpath.discovery.providers.base import LimitExceeded, Unavailable
    except ImportError:  # pragma: no cover
        return SourcePage(source_id, "error", error=SourceError("error", f"unexpected {type(exc).__name__}"))

    if isinstance(exc, (SourceDenied, policy.Denied)):
        status, code, message = "denied", "denied", str(exc)
    elif isinstance(exc, (SourceUnavailable, Unavailable, files_service.Disabled)):
        status, code, message = "unavailable", "unavailable", str(exc)
    elif isinstance(exc, NotSupported):
        status, code, message = "not-supported", "not-supported", str(exc)
    elif isinstance(exc, (ContractError, files_service.DiscoveryError, LimitExceeded)):
        status, code, message = "error", files_service.error_code(exc) if not isinstance(exc, ContractError) \
            else "invalid-request", str(exc)
    else:
        status, code, message = "error", "error", f"unexpected {type(exc).__name__} in this source"
    return SourcePage(source_id, status, error=SourceError(code, message[:300], status == "unavailable"))


def statuses(adapters: dict[str, SourceAdapter]) -> list[dict]:
    """The source registry: every adapter's status, a failing one reported rather than hidden."""
    out = []
    for source_id, adapter in adapters.items():
        try:
            out.append(adapter.status().to_dict())
        except Exception as exc:  # noqa: BLE001 - reported as the source's state
            page = error_page(source_id, exc)
            out.append({"source_id": source_id, "source_type": adapter.source_type, "state": "unavailable",
                        "reason": page.error.message})
    return out


def search(adapters: dict[str, SourceAdapter], filters: Filters, limit: int = 20,
           cursor: str | None = None) -> SearchResponse:
    """One page from each source in scope.

    With a cursor, only sources that returned a continuation are asked again; the rest are
    finished. ``filters.sources`` narrows the scope; an unknown name is reported, not ignored.
    """
    if not 1 <= limit <= MAX_LIMIT:
        raise ContractError(f"limit must be between 1 and {MAX_LIMIT}")
    continuing = decode_cursor(cursor)
    scope = list(filters.sources) or list(adapters)
    if continuing:
        scope = [s for s in scope if s in continuing]
    pages = []
    for source_id in scope:
        adapter = adapters.get(source_id)
        if adapter is None:
            pages.append(SourcePage(source_id, "unknown-source",
                                    error=SourceError("unknown-source", "no such source is registered")))
            continue
        try:
            page = adapter.search(filters, continuing.get(source_id), limit)
            if page.source_id != source_id:
                raise ContractError("adapter answered for a different source")
            if len(page.results) > limit:
                raise ContractError("adapter returned more results than the page limit")
        except KeyboardInterrupt:
            raise
        except Exception as exc:  # noqa: BLE001 - one source's failure must not erase the others
            page = error_page(source_id, exc)
        pages.append(page)
    return SearchResponse(filters, tuple(pages), limit, now())


def describe(adapters: dict[str, SourceAdapter], ref: str) -> dict:
    """Inspect one reference through its own source's adapter."""
    reference = Reference.parse(ref)
    adapter = adapters.get(reference.source_id)
    if adapter is None:
        raise UnknownReference(f"no source {reference.source_id!r} is registered")
    return adapter.describe(reference.native)


def close_all(adapters: dict[str, SourceAdapter]) -> None:
    for adapter in adapters.values():
        try:
            adapter.close()
        except Exception:  # noqa: BLE001 - closing must not mask the operation's own outcome
            pass
