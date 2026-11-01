"""Telling the owner about mail, inside the mailbox: important-mail alerts, a digest, and statistics.

- **Alerts.** When a conversation with a person newly needs the owner's reply, its latest message is flagged (or
  given the label "Important"), so the owner's mail app, which already notifies on flagged mail or that label if they
  set it up, tells them. Only mail that arrives after alerts are turned on.
- **Digest.** Daily or weekly, a short message put straight into the inbox (APPEND; nothing is sent): who is waiting
  for a reply, what the owner is waiting on, new mail by category, rule activity, and senders worth unsubscribing
  from.
- **Follow-up reminders.** When the owner wrote last and nobody has answered after a few days, the owner's message
  gets the label "Follow-up" (and the worker drafts a nudge); the label comes off when someone answers. Only
  conversations the owner wrote in during the month before the reminder would be due.
- **Reply labels.** Optionally, the latest message of each conversation waiting on the owner carries the label
  "To-reply", and of each conversation the owner is waiting on, "Awaiting-reply", so the mail app shows the same lists
  as Towpath; when a conversation moves on, the label moves or comes off. Conversations with mail in the month before
  the labels were turned on, and everything after.
- **Statistics.** Mail received and sent per week, how quickly the owner replies, and the people they write to most.

Alerts and digests are ordinary mail changes proposed by the worker and approved by the owner's own setting, so they
are dry runs while the account is in dry runs only or the kind of change (flag, label, note) is turned off.
"""

from towpath.mail.notify.common import (
    ALERT_MODES,
    DIGEST_EVERY,
    FOLLOW_UP_DAYS,
    TIMEZONE,
    alert_setting,
    auto_approved,
    digest_setting,
    follow_up_days,
    reply_labels_setting,
    set_alerts,
    set_digest,
    set_follow_up,
    set_reply_labels,
)
from towpath.mail.notify.digest import digests_pending
from towpath.mail.notify.statistics import measure, stats
from towpath.mail.notify.watching import alerts_pending, follow_up_due, follow_ups_pending, reply_labels_pending

__all__ = [
    "ALERT_MODES", "DIGEST_EVERY", "FOLLOW_UP_DAYS", "TIMEZONE", "alert_setting", "alerts_pending", "auto_approved",
    "digest_setting", "digests_pending", "follow_up_days", "follow_up_due", "follow_ups_pending", "measure",
    "reply_labels_pending", "reply_labels_setting", "set_alerts", "set_digest", "set_follow_up", "set_reply_labels",
    "stats",
]
