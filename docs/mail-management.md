# Mail management

Status: **designed, not built.** Nothing in this repository accesses or changes a live mailbox.

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

## Prompt injection and model output

Mail is untrusted input. In Towpath, model endpoints receive no tools; a model can suggest a category, a reason, or text, and the result records that it was model-produced. It cannot approve anything or reach the action runner. Inbox Zero's own handling of model output is governed by its rules and settings, which is one more reason to start it with limited actions.
