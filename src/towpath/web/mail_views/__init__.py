"""The Mail page: which accounts Towpath manages, what may change mail, what Towpath did, and a reader.

Everything here is the owner's choice or a record of what happened. Changes the owner asks for on a message
are recorded in the maildecisions store and carried out by the action runner in the connector. Until the owner
turns a kind of change on for an account (and adds its write password on the Connections page), each one is a
dry run.
"""

from towpath.web.mail_views.common import _account, _config, _subjects
from towpath.web.mail_views.page import (
    answer,
    answer_batch,
    draft_notes,
    dry_run,
    manage,
    model,
    overview,
    pause,
    settings_page,
    stats_page,
    tell_me,
    undo,
    writes,
)
from towpath.web.mail_views.reading import act, conversation, conversations, mark, messages, reader, save_draft

__all__ = [
    "_account", "_config", "_subjects", "act", "answer", "answer_batch", "conversation", "conversations",
    "draft_notes", "dry_run", "manage", "mark", "messages", "model", "overview", "pause", "reader", "save_draft",
    "settings_page", "stats_page", "tell_me", "undo", "writes",
]
