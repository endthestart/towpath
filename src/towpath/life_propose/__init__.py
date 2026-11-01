"""Proposing life-story events from mail the owner chose, through the AI model the owner allowed.

The owner asks on the Life page for one mail account and period (a decision in the decisions store). Each poll,
the worker reads a few likely messages from that slice (found by subject words, newest first), fetches only their
text through the account's read-only connector, and asks the model, through the inference gateway and its grants,
for the events each message gives evidence of. An event is kept only when its supporting quote appears verbatim in
the message, so every proposed claim cites real text. Proposals are resumable: each message examined is recorded.

Advertising never reaches the model when the provider has already filed it (Gmail's Promotions), and the model
says what kind of email it read: nothing is proposed from marketing or spam. Each event carries the model's view
of its significance, so everyday purchases can wait under Minor on the Life page. A new prompt version reads the
slice again: the owner's reviewed proposals stay as they are, and unreviewed ones are proposed afresh.
"""

from towpath.life_propose.candidates import _candidates, _likely, _stored_text, _text
from towpath.life_propose.choices import (
    auto,
    auto_progress,
    blocked,
    pause,
    progress,
    request,
    run_id,
    set_auto,
    set_request,
)
from towpath.life_propose.common import PROMPT_VERSION
from towpath.life_propose.worker import propose_pending

__all__ = [
    "PROMPT_VERSION", "_candidates", "_likely", "_stored_text", "_text", "auto", "auto_progress", "blocked", "pause",
    "progress", "propose_pending", "request", "run_id", "set_auto", "set_request",
]
