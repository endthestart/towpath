"""Read-only Gmail authorization: checking scopes, and keeping the token private."""

import json
import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from towpath.adapters.errors import AuthStop, ServerStop

READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
ALLOWED_SCOPES = {READONLY_SCOPE}
TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"


class ScopeError(PermissionError):
    pass


def check_scopes(granted: set[str]) -> None:
    """Refuse a token unless its grant is exactly read-only Gmail access."""
    if READONLY_SCOPE not in granted:
        raise ScopeError("the token does not include gmail.readonly")
    extra = granted - ALLOWED_SCOPES
    if extra:
        raise ScopeError(f"the token carries broader scopes than Towpath allows: {sorted(extra)}. "
                         "Use a Google Cloud project used only by Towpath, revoke this grant, and authorize again.")


def tokeninfo_scopes(access_token: str) -> set[str]:
    """Ask Google which scopes an access token actually carries."""
    url = TOKENINFO_URL + "?" + urllib.parse.urlencode({"access_token": access_token})
    with urllib.request.urlopen(url, timeout=20) as resp:  # noqa: S310 - fixed https URL
        info = json.load(resp)
    return set(info.get("scope", "").split())


def _write_private(path: Path, text: str) -> None:
    """Replace a token atomically with an owner-only file, including on refresh."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def authorize(client_secrets: Path, token_path: Path, open_browser: bool = True) -> dict:
    """Run Google's installed-app consent flow and store a verified read-only token."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets), scopes=[READONLY_SCOPE])
    creds = flow.run_local_server(port=0, open_browser=open_browser, include_granted_scopes="false",
                                  access_type="offline", prompt="consent")
    granted = tokeninfo_scopes(creds.token)
    check_scopes(granted)
    _write_private(Path(token_path), creds.to_json())
    return {"granted_scopes": sorted(granted), "token": str(token_path)}


def load_credentials(token_path: Path, verify_online: bool = True):
    """Load the saved token, refreshing it if needed. Failures become clean, sanitized stops."""
    from google.auth.exceptions import RefreshError, TransportError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    token_path = Path(token_path)
    if not token_path.is_file():
        raise AuthStop("no saved authorization for this source; run `towpath connect auth <source>`")
    creds = Credentials.from_authorized_user_file(str(token_path), scopes=[READONLY_SCOPE])
    try:
        if not creds.valid:
            creds.refresh(Request())
            _write_private(token_path, creds.to_json())
    except RefreshError:
        raise AuthStop("the saved authorization expired or was revoked (an app left in Testing status expires "
                       "after 7 days); run `towpath connect auth <source>`") from None
    except TransportError:
        raise ServerStop("could not reach Google to refresh the authorization; try again later") from None
    if verify_online:
        try:
            granted = tokeninfo_scopes(creds.token)
        except urllib.error.HTTPError as exc:
            raise AuthStop(f"Google could not confirm the token's scopes (HTTP {exc.code}); "
                           "run `towpath connect auth <source>`") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise ServerStop("could not reach Google to confirm the token's scopes; try again later") from None
        check_scopes(granted)
    return creds
