"""What the file discovery service shares: its errors, its configuration, and reaching a provider."""

from towpath.discovery import policy, refs
from towpath.discovery.providers import build
from towpath.discovery.providers.base import LimitExceeded, Unavailable
from towpath.discovery.records import Locator, Occurrence, RecordError


class DiscoveryError(RuntimeError):
    code = "error"


class Disabled(DiscoveryError):
    code = "disabled"


class NotFound(DiscoveryError):
    code = "not-found"


class Stale(DiscoveryError):
    code = "stale"


class Unverifiable(DiscoveryError):
    code = "unverifiable"


ERROR_CODES = {policy.Denied: "denied", Unavailable: "unavailable", refs.BadReference: "invalid-reference",
               LimitExceeded: "limit"}


def error_code(exc: BaseException) -> str:
    if isinstance(exc, DiscoveryError):
        return exc.code
    for kind, code in ERROR_CODES.items():
        if isinstance(exc, kind):
            return code
    return "error"


def files_config(config):
    if config.files is None:
        raise Disabled("file discovery is not configured (no [files] table)")
    if not config.files.enabled:
        raise Disabled("file discovery is disabled ([files] enabled = false)")
    return config.files


def provider(config, provider_id: str | None = None):
    fc = files_config(config)
    if not fc.providers:
        raise Unavailable("no files provider is configured")
    if provider_id is None:
        if len(fc.providers) > 1:
            raise DiscoveryError(f"several providers configured; choose one of {', '.join(fc.providers)}")
        provider_id = next(iter(fc.providers))
    if provider_id not in fc.providers:
        raise NotFound(f"no files provider {provider_id!r}")
    return build(fc.providers[provider_id], fc)


def _limit(fc, name: str, requested: int | None) -> int:
    ceiling = fc.limits[name]
    if requested is None:
        return ceiling
    if requested < 1:
        raise DiscoveryError(f"{name} must be at least 1")
    return min(requested, ceiling)


def _accept(hit, prov, roots: set[str], folders: refs.Folders | None = None,
            located: tuple[str, str] | None = None) -> Occurrence | None:
    """Turn an untrusted provider row into an occurrence, or None if it must not be shown. ``located`` is
    ``refs.locate``'s answer for this row when the caller already has it."""
    try:
        alias, rel = located or refs.locate(hit.url, prov.roots, folders)
    except refs.BadReference:
        return None
    if alias not in roots or refs.excluded(prov.roots[alias], rel):
        return None
    try:
        locator = Locator(prov.id, alias, rel, hit.members, hit.native_id)
        return Occurrence(locator, hit.extraction, hit.media_type, hit.size, hit.dates, hit.hashes, hit.version)
    except RecordError:
        return None
