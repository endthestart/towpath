"""The real Gmail API client and OAuth helpers (optional: pip install "towpath[gmail]").

The client implements the same five read-only calls as the fixture client.
Authorization requests only ``gmail.readonly``, disables incremental
authorization, and refuses any token that carries a broader grant.
"""

from towpath.adapters.google_gmail.authorizing import (
    READONLY_SCOPE,
    ScopeError,
    authorize,
    check_scopes,
    tokeninfo_scopes,
    _write_private,
)
from towpath.adapters.google_gmail.client import GoogleGmailClient, verify_structure

__all__ = [
    "GoogleGmailClient", "READONLY_SCOPE", "ScopeError", "_write_private", "authorize", "check_scopes",
    "tokeninfo_scopes", "verify_structure",
]
