"""Reading managed accounts' mail into the mail store: headers, flags, thread and text, for every message.

Runs in the connector, through the account's read-only IMAP connection (EXAMINE and BODY.PEEK only, so nothing is
marked read). Each worker poll reads one bounded slice per account: first messages indexed since the last slice,
then the rest of the account's history, newest indexed first. Progress is the range of the source index already
read (rowids, which only a VACUUM would renumber, and Towpath never vacuums), so a stop at any point resumes where it
left off, and nothing is read at startup.

Flags change without new mail (a message read on the phone, a change Towpath made). Each slice asks every managed
mailbox for the flags that changed since the last read (CONDSTORE), or, on a server without it, reads every flag at
most hourly.
"""

from towpath.mail.analysis.common import eligible, now, progress
from towpath.mail.analysis.worker import analyze_pending, build_search

__all__ = [
    "analyze_pending", "build_search", "eligible", "now", "progress",
]
