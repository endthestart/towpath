"""Read-only source adapters. Nothing in this package can change a source."""

from towpath.adapters.folder import FolderConnector
from towpath.adapters.gmail import FixtureGmailClient, GmailConnector


def build_connector(source, config=None, **options):
    """Build a read-only connector. Real Gmail connections always get the shared, persistent
    quota limiter and the per-budget lock from ``config``; there is no unpaced path."""
    if source.adapter == "fixture-gmail":
        return GmailConnector(source.id, FixtureGmailClient(source.path, **options))
    if source.adapter == "gmail":
        from towpath.adapters.google_gmail import GoogleGmailClient
        from towpath.quota import limiter_for

        if config is None:
            raise ValueError("a Gmail connector needs the configuration for its quota budget")
        limiter = limiter_for(config, source.id)  # takes the budget lock before any Google call
        try:
            client = GoogleGmailClient.from_token(source.token, limiter=limiter)
        except BaseException:
            limiter.close()
            raise
        connector = GmailConnector(source.id, client)
        connector.limiter = limiter
        return connector
    if source.adapter == "imap":
        from towpath.adapters.imap import ImapConnector, connect_session

        return ImapConnector(source.id, lambda: connect_session(source), source.mailboxes)
    if source.adapter == "paperless":
        from towpath.adapters.destinations import PaperlessConnector
        return PaperlessConnector(source)
    if source.adapter == "immich":
        from towpath.adapters.destinations import ImmichConnector
        return ImmichConnector(source)
    if source.adapter == "folder":
        return FolderConnector(source.id, source.kind, source.path)
    raise ValueError(f"unknown adapter {source.adapter!r}")


__all__ = ["FixtureGmailClient", "FolderConnector", "GmailConnector", "build_connector"]
