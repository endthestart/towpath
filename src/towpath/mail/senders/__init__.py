"""Senders and categories: who writes to the owner, how much, and what kind of mail it is.

The worker totals each sender's mail from the mail store (messages, unread, space, first and last dates, list and
unsubscribe headers, whether the owner ever wrote to them, how much is still in the inbox) and gives each sender a
category:

1. the owner's own category for the sender (``maildecisions``), which always wins and is how Towpath learns;
2. header rules: someone the owner has written to is a person; an automated address without a list is a
   notification;
3. the model, from the sender's name and address and a few recent subjects, busiest and most recent senders first.

A message's category is its sender's, except for account-security mail (sign-in codes, password resets) and
calendar invitations, which are recognised from the message itself. Cold email is a category too: a first-time
sender the owner never wrote to, selling or recruiting.
"""

from towpath.mail.senders.common import CATEGORIES, mark, marks
from towpath.mail.senders.reading import accounts, categories, inbox, listing, message_category, totals
from towpath.mail.senders.rebuilding import space
from towpath.mail.senders.sorting import categorize_pending

__all__ = [
    "CATEGORIES", "accounts", "categories", "categorize_pending", "inbox", "listing", "mark", "marks",
    "message_category", "space", "totals",
]
