"""Read-only IMAP connector, built on IMAPClient (BSD-3-Clause; see docs/evaluations/imap-client.md).

Read-only by construction:

- Towpath talks to the server only through :class:`ReadOnlySession`, which exposes LOGIN, CAPABILITY,
  LIST, EXAMINE, UID SEARCH (ALL, or TEXT with the query words as values), UID FETCH and LOGOUT.
  The IMAPClient object, with its STORE, COPY, MOVE, EXPUNGE, APPEND and folder commands, is never
  handed out.
- Mailboxes are opened with EXAMINE (RFC 9051 section 6.3.3). A server that answers EXAMINE with
  READ-WRITE is refused.
- FETCH items are checked against an allow-list. Body sections must use ``BODY.PEEK``, which does
  not set ``\\Seen`` (RFC 9051 section 6.4.5); ``BODY[...]``, ``RFC822`` and ``RFC822.TEXT`` are refused.
- Mail management also uses ENABLE CONDSTORE, FETCH with ``CHANGEDSINCE`` (only flags that changed), SEARCH for one
  Message-ID, and IDLE to learn of new mail. None of them changes a message.

Identity is mailbox + UIDVALIDITY + UID (RFC 9051 section 2.3.1.1). When UIDVALIDITY changes,
old references are not mapped onto new UIDs: the mailbox is listed afresh, and the old items are
marked absent by the sync engine's confirm step only after this connector reports them gone.
"""

from towpath.adapters.imap.connector import ImapConnector
from towpath.adapters.imap.session import LOOPBACK, ReadOnlySession, ReadOnlyViolation, connect_session, _mapped_errors
from towpath.adapters.imap.structure import flatten_structure

__all__ = [
    "ImapConnector", "LOOPBACK", "ReadOnlySession", "ReadOnlyViolation", "_mapped_errors", "connect_session",
    "flatten_structure",
]
