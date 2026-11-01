"""The action runner for mailbox changes (role ``act``): the only code that holds an account's write password.

Inputs are changes the owner asked for directly (``maildecisions`` requests) and proposals the owner approved
(``mailplan`` proposals with a matching answer in ``maildecisions``). It ignores classifications,
model output and anything else. Before acting it checks the digest, the type, the owner's per-type switch and the
message itself (identity, and Message-ID where known). While the account's "dry runs only" switch is on, or for
an action type the owner hasn't turned on, or an account without a write password, a change runs as a **dry
run**: the message is checked through the read-only connection and the receipt says what would have changed.
Every request gets exactly one receipt; a receipt is never replaced.

:class:`ActSession` is the whole write surface: SELECT, UID STORE of ``\\Seen``, ``\\Flagged`` and labels (never
``\\Deleted``), UID MOVE to an existing mailbox, APPEND to Drafts or the inbox, and UNSELECT. There is no EXPUNGE,
CLOSE, DELETE, COPY, CREATE or RENAME, and the IMAP client is never handed out.
"""

from towpath.mail.executor.checks import pending, receipts
from towpath.mail.executor.runner import run_pending
from towpath.mail.executor.session import ActSession, WriteViolation, connect_act, password_path

__all__ = [
    "ActSession", "WriteViolation", "connect_act", "password_path", "pending", "receipts", "run_pending",
]
