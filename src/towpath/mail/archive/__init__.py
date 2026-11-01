"""Archiving mail to a file, and removing it from the server only once each copy is verified.

The owner picks messages (on the cleanup pages) and asks for an archive, with or without removal from the server
(``maildecisions`` archive requests). The connector then, in bounded slices:

1. reads each whole message through the read-only connection (``BODY.PEEK[]``, so nothing is marked read);
2. appends it to ``archive/mail/<account>/<request>.mbox`` in the data folder (mboxrd: ``From `` lines escaped),
   flushed to disk;
3. reads it back from the file, undoes the escaping and checks the bytes' SHA-256, the size the server reported and
   the Message-ID against what Towpath read before; only then records it as archived (mail store ``archived``);
4. if the owner asked for removal, proposes moving it to Trash (``archive:<request>``). The action runner carries
   that out without a further question only for a message with a verified copy, and only when moving to Trash is
   turned on and the account isn't in dry runs only. Towpath never expunges; the provider empties its Trash.

Archived messages stay in Towpath's mail store, so they remain searchable after they leave the server.
"""

from towpath.mail.archive.common import MAX_ITEMS, root
from towpath.mail.archive.copies import append, check_copy, escape, unescape, verify
from towpath.mail.archive.owner_requests import progress, request, requests
from towpath.mail.archive.worker import archive_pending, auto_approved

__all__ = [
    "MAX_ITEMS", "append", "archive_pending", "auto_approved", "check_copy", "escape", "progress", "request",
    "requests", "root", "unescape", "verify",
]
