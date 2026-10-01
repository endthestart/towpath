"""Read-only source adapters. Nothing in this package can change a source."""

from towpath.adapters.folder import FolderConnector
from towpath.adapters.gmail import FixtureGmailClient, GmailConnector


def build_connector(source, **options):
    if source.adapter == "fixture-gmail":
        return GmailConnector(source.id, FixtureGmailClient(source.path, **options))
    if source.adapter == "gmail":
        from towpath.adapters.google_gmail import GoogleGmailClient
        return GmailConnector(source.id, GoogleGmailClient.from_token(source.token))
    if source.adapter == "folder":
        return FolderConnector(source.id, source.kind, source.path)
    raise ValueError(f"unknown adapter {source.adapter!r}")


__all__ = ["FixtureGmailClient", "FolderConnector", "GmailConnector", "build_connector"]
