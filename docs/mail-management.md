# Mail management

Status: **changing, per [ADR 0002](adrs/0002-towpath-native-mail-management.md) (proposed 2026-10-16).** Towpath is building its own mail management over IMAP, Fastmail first, following the [build plan](plans/2026-10-16-native-mail-management.md). Inbox Zero keeps the owner's Gmail until Towpath reaches parity there. The foundation (milestone 1) is described [below](#towpath-native-mail-management); the rest of this page records the Inbox Zero integration that applies until then.

## Approach

Mail management needs write access to the mailbox: labeling, archiving, unsubscribing, drafting, and rules all change it. The life stream does not. So the two are handled differently:

- **Mail management uses an integrated mail-management tool** that already does the work well, running as an optional service in Towpath's Docker Compose file and holding its own Gmail write access. The first is [Inbox Zero](https://github.com/elie222/inbox-zero) ([D15](decisions.md#d15-role-of-inbox-zero)). Towpath integrates with it rather than rebuilding it.
- **Towpath's own mail access is read-only** (`gmail.readonly` in `towpath-connect`, from a Google Cloud project separate from the mail-management tool's), used for the life stream and for finding attachments to route to other tools.

The integration sits behind a *mail-management provider* interface, so an alternative tool can be added, or Towpath can build its own features and replace the provider later, without changing the rest of Towpath.

## Who does what

| Feature | Provided by | How it appears in Towpath |
| --- | --- | --- |
| Categorize, with corrections | Inbox Zero | Link to Inbox Zero |
| Important and unanswered | Inbox Zero (reply tracking) | Link to Inbox Zero |
| Unsubscribe | Inbox Zero (bulk unsubscriber) | Link to Inbox Zero |
| Draft replies | Inbox Zero (drafts created in Gmail) | Link to Inbox Zero; drafts also visible in Gmail |
| Smart rules and digest | Inbox Zero | Rules listed read-only from its API; digest by link |
| Activity overview | Inbox Zero statistics | Read from its API |
| Route PDFs and photos to a document system or photo library | Towpath | [Scans and destinations](scans-and-destinations.md); Inbox Zero files only to cloud drives |
| Search across mail and other sources | Towpath | Full-text search over Towpath's index, linked to life-stream evidence |

The [provider contract](interfaces.md#6-mail-management-provider) lists the exact Inbox Zero endpoints behind each row. Where no endpoint exists, Towpath links to Inbox Zero's own screen and does not present the feature as integrated.

## Integration points

| Point | Purpose | Status |
| --- | --- | --- |
| Compose profile `mail` | Runs Inbox Zero with its database and cache; or Towpath points at an existing instance | To build |
| Google Cloud projects | **Separate projects** for Inbox Zero (its write scopes) and Towpath (`gmail.readonly`). Google treats consent as belonging to a project, and incremental authorization can combine grants across clients in one project, so separate tokens alone are not enough evidence of separation. Towpath checks the scopes actually granted at connection time and refuses anything beyond `gmail.readonly` | To document and test |
| Inbox Zero API | Statistics and rules, read-only, with a key scoped to `STATS_READ` and `RULES_READ` | Checked in source; to exercise |
| Webhook action | Optional hints to Towpath when a rule runs | Checked in source; requires a setting that disables Inbox Zero's SSRF protection for private addresses; to evaluate |
| Model roles | Every Inbox Zero model role set explicitly (below) | To configure and verify |
| Links | Deep links into Inbox Zero's screens | To build |

## Caveats

- **Automatic actions.** Inbox Zero's rules run automatically and can label, archive, draft, reply, forward, send, or delete, depending on how they are configured. Towpath's approve-before-act model does not apply inside Inbox Zero; its own settings do. The setup guide should recommend starting with rules limited to labeling, archiving, and drafting.
- **Model policy.** Pointing Inbox Zero at a local endpoint controls where its requests go, but does not enforce Towpath's item-level model use: it cannot know that a message is `excluded` or `local-only`. Inbox Zero has five model roles (default, economy, chat, draft, and a lightweight role for classification), each an ordered list with fallbacks, and unset roles fall back to others. Setup must set every role and every fallback explicitly, and the evaluation must confirm no request reaches any other endpoint, before Towpath makes any privacy statement about it. Its optional sensitive-data setting redacts or blocks likely credentials and card numbers; it is not item-level exclusion.
- **Who controls what.** Towpath's screens label each control with the application that owns it, so it is clear when a setting belongs to Inbox Zero.
- **Its own copy of data.** Inbox Zero stores data about mail in its own database. That data belongs to Inbox Zero's service, not to Towpath's stores, and is backed up or deleted with it.
- **License.** Inbox Zero is *source available with use restrictions*: AGPL-3.0 with added terms restricting commercial monetization and requiring an enterprise license for organizations of five or more business users, with exemptions including personal, educational, and research use and smaller organizations. These terms conflict with the [Open Source Definition](https://opensource.org/osd). Under the [license policy](integrations.md) it is an optional profile, labeled with its terms; Towpath's code does not depend on or copy it.
- **Fit must be confirmed.** A focused evaluation on a dedicated test mailbox comes before use with a primary account ([evaluation plan](evaluations/inbox-zero-plan.md)).

## If Towpath builds its own features later

If Inbox Zero stops fitting, or a Towpath-native feature replaces part of it, the earlier design applies. It is kept here so the provider interface does not paint Towpath into a corner.

**Action tiers**

| Tier | Examples | Reversible? | Effects outside the account |
| --- | --- | --- | --- |
| 0. Towpath only | Tags, notes, triage status | Yes | None |
| 1. Message state | Labels, mark read, archive | Yes, by the inverse action | None |
| 1d. Draft | Create a draft reply | Yes, delete the draft | None until sent |
| 2. Standing rules | Create a filter | Yes, but silently affects future mail | None |
| 3. External requests | Unsubscribe by one-click POST or mail | No | Confirms the address is live to the sender |
| 4. Trash | Move to Trash | Within the retention window | None |
| 5. Permanent deletion | Delete bypassing Trash | No | None |

**Provider permissions.** No Gmail scope allows labeling or archiving without also allowing sending (`gmail.modify`); drafts need `gmail.compose`, which also sends; filters need `gmail.settings.basic` ([tooling review](research/2026-10-tooling-review.md#gmail-access)).

**Options for executing changes.** Review-only export; writes inside the service that parses mail (weakest); a separate action runner with its own credential and allowlist (`towpath-act`); an external executor such as an integrated tool; or provider-native filters the person installs. Towpath now uses the integrated-tool option for mailbox changes and keeps `towpath-act` for deliveries to destinations ([D1](decisions.md#d1-mailbox-and-destination-execution)).

**Action runner contract** (applies to `towpath-act` today for deliveries, and to any future Towpath mailbox actions):

- Inputs: approval records with their frozen proposals, and the runner's own allowlist. It ignores classifications, model output, and life-stream data.
- Before acting: verify the proposal digest; refuse action types not allowlisted and expired proposals; recheck each target.
- While acting: rate-limit; stop the batch on an unexpected error class; never retry a non-idempotent action without a fresh recheck.
- After acting: write a receipt per item; generate an inverse proposal where one exists; return changed or failed items for review.
- If it ever holds a Gmail write token: allow only specific API calls, with tests asserting that no send or delete call exists in its code path ([R16](decisions.md#recommendations-in-this-design)).

## Towpath-native mail management

Built in milestone 1 of the [build plan](plans/2026-10-16-native-mail-management.md); qualification on the owner's server is pending.

| Piece | Role and store | What it does |
| --- | --- | --- |
| Managed accounts | web → decisions (`mail-manage` grant) | Nothing below runs for an account until the owner turns management on for it on the Mail page. IMAP accounts only, for now. |
| Reading | connect → `mail` | Every message's headers, flags, thread (the server's `THREADID` where offered, otherwise `References`), list headers and new text (quotes removed, at most 20,000 characters), read with EXAMINE and `BODY.PEEK` in resumable slices: new mail first, then the whole history. Flags that changed are read with CONDSTORE. |
| Watching | connect | IDLE on each managed inbox; new mail starts indexing, then reading. |
| Owner's decisions | web → `maildecisions` | Changes asked for on a message (and Undo), and answers to proposals, each tied to the digest of exactly what was asked. Which kinds of change may run, per account (decisions store); all start off. |
| Conversations | worker → `mailintel` | Reply tracking: each conversation is *to reply*, *awaiting reply*, *FYI* or *done*. Header rules first (mailing lists and automated senders are FYI; who wrote last; age), then the model reads conversations with people, newest first, across the whole history. The owner's mark (in `maildecisions`) outranks both. For recent conversations waiting on the owner, the model drafts a reply in the owner's voice, with a few of their sent messages as examples; the owner edits it and saves it to Drafts (a `draft` change). Drafts may use the owner's own notes (availability, phone number, sign-off), kept in the decisions store. |
| Senders and categories | worker → `mailintel` | Every sender's totals (mail, unread, space, dates, inbox count, unsubscribe headers, whether the owner wrote to them) and a category: the owner's choice (`maildecisions`, applies to all their mail), else rules (someone the owner wrote to is a person; automated addresses are notifications), else the model. Account-security mail and calendar invitations are recognised per message. Cold email is a category: a first contact from a stranger. |
| Rules | web → `maildecisions`; worker → `mailintel`, `mailplan` | The owner's rules: patterns on sender, recipients, subject and text, categories and mailbox, plus an optional condition in words the model checks; actions are mail changes. Each rule version looks at each message once (new mail, or the whole history). A match proposes its actions; the owner approves them one by one or per rule, or switches the rule to automatic (reversible changes only). Templates: cold email, newsletters, marketing, receipts, notifications. A rule can also be described in plain English (queue `rule_requests`): the worker's model drafts it without seeing any mail, and the owner checks, previews and saves it in the ordinary form. |
| Proposals | worker → `mailplan` | Changes Towpath's features propose. Immutable; grouped by rule for approval on the Mail page. |
| Search and space | connect → `mail`; worker → `mailintel` | Full-text search over Towpath's copy of each message (FTS5, kept by triggers), including mail archived to a file and gone from the server. Space by category, year, folder, sender and largest message. |
| Clean-up | web → `maildecisions` | Pick mail by folder, age, sender, category and read state; move it to the Archive folder (ordinary `archive` changes), or archive it to a file, with or without removal from the server. |
| Archives | connect → `mail`, worker → `mailplan` | Whole messages read with `BODY.PEEK[]` and appended to `archive/mail/<account>/<request>.mbox` (mboxrd) in the data folder; each copy is read back and its SHA-256, size and Message-ID checked before it counts. With removal asked for, each verified message gets a `trash` proposal, approved by the request itself. |
| Attachments | web reads `source` | Every attachment from the message structure the connector indexed (nothing more is read from the server): name, kind, size, sender and date, each message once however many folders hold it. Small pictures inside a message's text are left out; signatures and keys only when asked for. |
| Alerts, digest, statistics | worker → `mailintel`, `mailplan` | When a conversation with a person newly needs the owner's reply, its latest message is flagged or labelled "Important" (the owner's mail app notifies on those). A daily or weekly digest is put straight into the inbox (APPEND). Follow-up reminders: when the owner wrote last and nobody answered after 3, 7 or 14 days, their message is labelled "Follow-up" and a nudge is drafted; the label comes off when someone answers. All are proposals approved by the owner's setting, so dry runs follow as usual. Statistics: mail per week, the owner's median reply time, the people they write to most. |
| Unsubscribe | act | RFC 8058 one-click (HTTPS POST to public addresses, no redirects) or an email to the list's `mailto:` address over the provider's SMTP (Fastmail) with the write password; a sender offering only a web page gets a link. Optionally archives the sender's inbox mail and adds an automatic rule for their future mail. |
| Action runner | act → `mailact` | The only code with the write password. Checks the digest, approval, type and the message itself, then carries the change out, or, for a kind not turned on or an account without a write password, checks the message read-only and records a **dry run**. One receipt per request, with the inverse change for Undo. |

The write session (`towpath.mail.executor.ActSession`) offers only SELECT, UID STORE of `\Seen`, `\Flagged` and labels (never `\Deleted`), UID MOVE to an existing mailbox, APPEND to Drafts or the inbox, and UNSELECT (or EXAMINE, never CLOSE). There is no EXPUNGE, DELETE, COPY, CREATE or RENAME. Moving to Trash is the furthest a change goes; the provider's own Trash retention deletes. The write password is a second app password, entered on the account's Connections page, refused if it is the read-only one or can only read.

Gmail over IMAP (milestone 6, built 2026-10-17; connecting it waits for the owner's app password): a *Gmail (IMAP)*
connection reads All Mail and the Inbox by default; threads come from `X-GM-THRID`, labels are Gmail labels
(`X-GM-LABELS`), archiving moves to All Mail, and nothing is ever moved out of All Mail except to Trash (in Gmail that
would delete it): putting a message back in a folder, as Undo does, adds the label instead. Sender totals count a
message filed in two folders once.

Models: an AI model connection is marked as running on the owner's network (the default) or at a cloud service, and can offer further models on the same server and token (*Other models on this server*). The Mail page chooses the model for each mail feature (`mail.thread`, `mail.draft`, `mail.classify`) and whether it may read email text; nothing personal reaches any model without that grant. A whole account can be set to *Dry runs only* (the default), whatever kinds of change are ticked. A sender, or one message, can be *kept from cloud models* (`model_use = no-cloud` on `sender:<account>:<address>` or the item): every model call about mail names both, and the gateway refuses such mail to an endpoint at a cloud service while models on the owner's network still read it.

## Prompt injection and model output

Mail is untrusted input. In Towpath, model endpoints receive no tools; a model can suggest a category, a reason, or text, and the result records that it was model-produced. It cannot approve anything or reach the action runner. Inbox Zero's own handling of model output is governed by its rules and settings, which is one more reason to start it with limited actions.
