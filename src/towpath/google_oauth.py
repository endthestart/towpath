"""Google sign-in for Gmail from the Connections page (towpath-connect only).

The standard web-server flow with PKCE: Towpath sends the browser to Google's consent screen asking for
``gmail.readonly`` only, Google returns a one-time code to /connections/google/callback, and the connector
exchanges it for a token. The token's scopes are confirmed with Google before it is stored; a broader grant
is refused. See docs/specs/connections-in-the-ui.md.
"""

from pathlib import Path

from towpath.adapters.google_gmail import READONLY_SCOPE, ScopeError, check_scopes, tokeninfo_scopes
from towpath.connections import GOOGLE_CLIENT_FILE, ConnectionProblem


def _flow(credentials_dir: Path, redirect_uri: str, code_verifier: str | None = None):
    from google_auth_oauthlib.flow import Flow

    path = Path(credentials_dir) / GOOGLE_CLIENT_FILE
    if not path.is_file():
        raise ConnectionProblem("Enter your Google OAuth client first.")
    return Flow.from_client_secrets_file(str(path), scopes=[READONLY_SCOPE], redirect_uri=redirect_uri,
                                         code_verifier=code_verifier, autogenerate_code_verifier=code_verifier is None)


def consent_url(credentials_dir: Path, redirect_uri: str) -> tuple[str, str, str]:
    """Google's consent address, the anti-forgery state and the PKCE verifier to keep until the callback."""
    flow = _flow(credentials_dir, redirect_uri)
    url, state = flow.authorization_url(access_type="offline", prompt="consent", include_granted_scopes="false")
    return url, state, flow.code_verifier


def finish(credentials_dir: Path, redirect_uri: str, code: str, code_verifier: str) -> str:
    """Exchange Google's code for a token, confirm it is read-only, and return it as JSON for storage."""
    flow = _flow(credentials_dir, redirect_uri, code_verifier)
    try:
        flow.fetch_token(code=code)
    except Exception as exc:  # noqa: BLE001 - library errors can echo request details; keep only the type
        raise ConnectionProblem(f"Google didn't accept the sign-in ({type(exc).__name__}). Try connecting again.") \
            from None
    creds = flow.credentials
    if not creds.refresh_token:
        raise ConnectionProblem("Google didn't return a lasting sign-in. Remove Towpath's access in your Google "
                                "account settings, then connect again.")
    try:
        check_scopes(tokeninfo_scopes(creds.token))
    except ScopeError as exc:
        raise ConnectionProblem(f"Refused: {exc}") from None
    except OSError:
        raise ConnectionProblem("Couldn't reach Google to confirm the sign-in. Try again.") from None
    return creds.to_json()


def address(config, source_id: str, token_file: Path) -> str | None:
    """The account's address (users.getProfile, one quota unit, paced like every Gmail call). ``None`` if Gmail
    is busy with another job; the address is then filled in later."""
    from towpath.adapters.errors import SyncStop
    from towpath.adapters.google_gmail import GoogleGmailClient
    from towpath.quota import limiter_for

    try:
        limiter = limiter_for(config, source_id)
    except SyncStop:
        return None
    try:
        client = GoogleGmailClient.from_token(token_file, limiter=limiter)
        return client.get_profile().get("emailAddress")
    except SyncStop:
        return None
    finally:
        limiter.close()
