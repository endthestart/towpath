"""Gmail-shaped connector.

``GmailConnector`` implements Towpath's connector interface (describe, probe,
enumerate, fetch) on top of a small read-only client interface that mirrors
the Gmail API calls Towpath uses. ``FixtureGmailClient`` serves synthetic data
in Gmail's response shapes; a client for the real API can replace it later.
The client interface has no calls that change a mailbox.
"""

from towpath.adapters.gmail.connector import GmailConnector
from towpath.adapters.gmail.fixture_client import FixtureGmailClient

__all__ = [
    "FixtureGmailClient", "GmailConnector",
]
