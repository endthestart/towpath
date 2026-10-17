# ADR 0002: Towpath-native mail management over IMAP

- Date: 2026-10-16
- Status: Proposed; owner review of this ADR and the [build plan](../plans/2026-10-16-native-mail-management.md) comes before code
- Basis: Owner's twelve-answer interview of 2026-10-16; the Oct 12 week plan's "next mail milestone"
- Supersedes: [D15](../decisions.md#d15-role-of-inbox-zero) once Gmail parity is qualified; revises [D1](../decisions.md#d1-mailbox-and-destination-execution)

## Context

Inbox Zero runs on Hub for the owner's Gmail, using local models. Self-hosting it needs a separate Google Cloud
project, consent screen, OAuth client, Pub/Sub topic and People API, and it covers only Gmail and Outlook. That
works against Towpath's ease-of-use goal and leaves Fastmail and other IMAP accounts unmanaged. Towpath already
indexes every connected mailbox. Mail management is the step that turns that index into action, across all of a
person's mail and its whole history, not only new arrivals.

## Decision

1. **Towpath builds its own mail management**, reimplementing the behavior of Inbox Zero's features from their
   observable behavior and published documentation. No Inbox Zero code, prompts or schema text is copied (AGPL with
   added terms; Towpath is MIT).
2. **IMAP is the one mailbox path**, for every account. Fastmail comes first; Gmail follows over IMAP, using its
   label and thread extensions. Gmail-only features (filters, push) stay out until the IMAP path is qualified.
3. **Scope** is all mail in each account, across its full history: plain-language rules with static conditions;
   categories with corrections and learned sender patterns; reply tracking (to reply, awaiting reply); AI drafts;
   bulk unsubscribe; bulk archive (Clean); a cold-email blocker; a digest; statistics; attachment filing through
   Towpath's destinations; and space cleanup that archives old mail to a local, searchable mbox before removing it
   from the server. Meeting briefs, the assistant chat and Slack/Telegram channels are out.
4. **Writes go through a separate action runner** (`towpath-act`, with its own credential), never through the read
   connector, under the [action runner contract](../mail-management.md#if-towpath-builds-its-own-features-later). It
   allows only a fixed set of IMAP commands: flag and keyword STORE, MOVE, APPEND to Drafts and Inbox, and nothing
   that expunges outside Trash. SMTP is used only for approved `mailto:` unsubscribes; reply drafts are saved to
   Drafts for the owner to send.
5. **Autonomy is earned per rule.** Every new account starts in dry run: actions are proposed and logged. The owner
   turns writes on separately for each action type. A rule starts by proposing; after review the owner may switch it
   to automatic, but only for reversible actions (label, archive, mark read, move, draft). Unsubscribing, sending
   and trashing always need a batch approval.
6. **Removing mail from the server** happens only after the mail is archived to an mbox on Hub, read back, matched
   message by message against the server (Message-ID and size), and indexed and searchable in Towpath. Then
   Towpath moves it to Trash. Towpath never expunges. Permanent deletion is the provider's own Trash retention,
   so D1's rule that permanent deletion is never a Towpath action still holds. The owner accepts a Hub-only
   archive copy for now, ahead of the 3-2-1 backup work.
7. **Local models by default.** The owner's local endpoints are the default and preferred route. Cloud models are
   an explicit opt-in available to anyone who wants it, per install and per account, recorded with each result.
   Mail marked restricted never goes to a cloud model.
8. **Alerts stay in the mailbox.** Important mail is flagged or placed where the mail app already notifies, and the
   digest is APPENDed to the inbox. No new notification service.
9. **Inbox Zero keeps Gmail until parity.** Towpath writes only to Fastmail until Gmail over IMAP is qualified.
   It then runs in dry run on Gmail next to Inbox Zero for comparison, and the owner retires Inbox Zero. D15 is
   superseded at that point, not before.

## Consequences

- `ReadOnlySession` stays read-only. The act credential is a separate app password, and tests assert the runner's
  command allowlist.
- Full-history processing needs message text in a local analysis store, filled by resumable, pausable worker jobs
  under the week-plan rules (no work at startup, rollback tested).
- Reply tracking needs threads. On IMAP these come from `THREADID` (RFC 8474) where offered, otherwise from
  `References` and `In-Reply-To`.
- Standing rules run in Towpath's worker, because Fastmail's own rules are not editable over IMAP.
- The [mail-management doc](../mail-management.md), the [roadmap](../roadmap.md) and the [provider
  contract](../interfaces.md#6-mail-management-provider) are updated as milestones land.
