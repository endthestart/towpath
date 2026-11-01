"""Contacts and calendars connections: test, add, and sync (connector only, like towpath.connections).

A connection (adapter ``dav``) holds one app password that can read both CardDAV and CalDAV; Fastmail's servers are
filled in, and iCloud's are found from its standard starting addresses. Syncing lists every address book and
calendar, compares ETags, reads only what changed with a multiget, and removes what a complete listing no longer
shows. The owner starts it, or puts it on a schedule, on the Connections page, as for mail.
"""

from towpath.dav_sync.reading import birthdays_ahead, events, names, week_ahead
from towpath.dav_sync.syncing import PROVIDERS, add, discover, sync_pending, test

__all__ = [
    "PROVIDERS", "add", "birthdays_ahead", "discover", "events", "names", "sync_pending", "test", "week_ahead",
]
