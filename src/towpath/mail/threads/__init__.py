"""Conversations: who needs to reply (reply tracking), and reply drafts.

The worker groups the mail store's messages into conversations by thread and gives each one a state:

- ``needs_owner_reply``: someone wrote to the owner and is waiting for an answer;
- ``awaiting_others``: the owner wrote last and is waiting for an answer;
- ``fyi``: information only, including automated and mailing-list mail;
- ``done``: concluded.

Header rules decide first (mailing lists and automated senders are FYI; who wrote last; age). For a conversation
with a person, the model then reads the conversation (first message, last eight, the latest in full; quoted text was
already removed) and decides, newest conversations first, across the whole history. The owner's own mark on a
conversation outranks both, and is kept in ``maildecisions``.

For recent conversations waiting on the owner, the model drafts a reply in the owner's voice (a few of the owner's
own sent messages are its examples). Drafts stay in Towpath until the owner saves one to the mailbox's Drafts
folder, which is an ordinary mail change: a dry run until the owner turns it on.

Mail text is untrusted: prompts say so, the model gets no tools, and its answers are only a state, a reason and a
draft's text.
"""

from towpath.mail.threads.building import mark, marks, _messages, owner, sent_mailboxes
from towpath.mail.threads.classify import blocked, _classify, _failure, item_refused, _owner_examples, ready
from towpath.mail.threads.common import (
    BUSY_SECONDS,
    DRAFT_DAYS,
    LABELS,
    NOREPLY,
    RETRY_SECONDS,
    STATES,
    TASK_DRAFT,
    TASK_STATE,
)
from towpath.mail.threads.drafts import NOTES_MAX, _draft, notes, set_notes
from towpath.mail.threads.reading import counts, draft_message, listing, thread
from towpath.mail.threads.worker import _progress, _set_progress, track_pending

__all__ = [
    "BUSY_SECONDS", "DRAFT_DAYS", "LABELS", "NOREPLY", "NOTES_MAX", "RETRY_SECONDS", "STATES", "TASK_DRAFT",
    "TASK_STATE", "_classify", "_draft", "_failure", "_messages", "_owner_examples", "_progress", "_set_progress",
    "blocked", "counts", "draft_message", "item_refused", "listing", "mark", "marks", "notes", "owner", "ready",
    "sent_mailboxes", "set_notes", "thread", "track_pending",
]
