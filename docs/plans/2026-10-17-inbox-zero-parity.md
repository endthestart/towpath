# Inbox Zero parity checklist

Inbox Zero stays on Hub for Gmail until Towpath reaches parity, then it is retired
([ADR 0002](../adrs/0002-towpath-native-mail-management.md), superseding D15 at that point). This is the list to check
at that point. Dropped by the owner on October 16: meeting briefs and calendar, assistant chat, Slack and Telegram.

| Inbox Zero feature | Towpath | State |
| --- | --- | --- |
| Rules in plain English, with label, archive, mark read, star, move, draft | Rules: templates, patterns, a condition in words the model checks, or a rule described in plain English and drafted by the model | Built |
| Rules run automatically | Rules propose; automatic per rule, reversible changes only (never Trash or unsubscribe) | Built, stricter by design |
| Reply, forward, send, webhook actions | Not offered: Towpath never sends mail except an unsubscribe the owner asked for | Not planned |
| Rule testing and history | Preview on recent mail; history per rule version | Built |
| Reply Zero: *To reply* and *Awaiting reply* | Conversations, by rules and the model, the owner's mark always wins; shown in Towpath | Built |
| Reply Zero labels in the mail app | Optional *To-reply* and *Awaiting-reply* labels that move with the conversation; alerts; *Follow-up* | Built |
| Drafted replies in the owner's voice | Drafts from the owner's sent mail and their notes; saved to Drafts when the owner asks | Built (not saved automatically, by design) |
| Knowledge base and writing style | Notes for drafts on the Mail page | Built |
| Follow-up reminders | Label *Follow-up* and a drafted nudge after 3, 7 or 14 days | Built |
| Smart categories | Twelve categories by rules, the model, the owner; learned per domain | Built |
| Cold email blocker | *Cold email* category and its rule template | Built |
| Bulk unsubscribe | Unsubscribe page: one-click, by email, or a page to open | Built |
| Bulk archive and Clean | Clean up by category and age; archive to a file, verified, then Trash | Built |
| Email analytics | Statistics: volume, reply time, correspondents; Space; Attachments | Built |
| Digest email | Daily or weekly digest put into the inbox | Built |
| Attachment filing to a drive | Attachments page lists every file | Waiting on open question 1 (where to file) |
| Gmail | Gmail over IMAP with threads and labels, archive to All Mail | Built against the synthetic server; needs a Gmail app password to run beside Inbox Zero |

**Retirement test:** with Gmail connected over IMAP, run both side by side for a week and compare *To reply*,
categories and unsubscribe candidates; retire Inbox Zero once the owner prefers Towpath's lists and Gmail write
changes have run live without surprises.
