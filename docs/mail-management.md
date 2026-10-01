# Mail management

Status: **designed, not built.** Nothing in this repository accesses or changes a live mailbox.

## Approach

Mail management needs write access to the mailbox: labeling, archiving, unsubscribing, drafting, and rules all change it. The life stream does not. So the two are handled differently:

- **Mail management uses an integrated mail-management tool** that already does the work well, running as an optional service in Towpath's Docker Compose file and holding its own Gmail write access. The first is [Inbox Zero](https://github.com/elie222/inbox-zero) ([D15](decisions.md#d15-role-of-inbox-zero)). Towpath integrates with it rather than rebuilding it.
- **Towpath's own mail access is read-only** (`gmail.readonly` in `towpath-connect`), used for the life stream and for finding attachments to route to other tools.

The integration sits behind a *mail-management provider* interface, so an alternative tool can be added, or Towpath can build its own features and replace the provider later, without changing the rest of Towpath.

## Who does what

| Feature | Provided by | Notes |
| --- | --- | --- |
| Categorize, with corrections | Inbox Zero | Its rules and learned corrections |
| Important and unanswered | Inbox Zero | Its reply tracking ("to reply", "awaiting reply") |
| Unsubscribe | Inbox Zero | Its bulk unsubscriber |
| Draft replies | Inbox Zero | Drafts created in Gmail; sending stays with the person, per the rules they configure |
| Smart rules and digest | Inbox Zero | Its rules and digest action |
| Route PDFs and photos to a document system or photo library | Towpath | [Scans and destinations](scans-and-destinations.md); Inbox Zero files only to cloud drives |
| Search across mail and other sources | Towpath | Full-text search over Towpath's index, linked to life-stream evidence |
| Overview in Towpath's front end | Towpath | Status, links into Inbox Zero, and summaries read from its API |

## Integration points

| Point | Direction | Purpose | Status |
| --- | --- | --- | --- |
| Compose profile `mail` | — | Runs Inbox Zero with its database and cache; can instead point at an existing instance | To build |
| Google Cloud project | Shared setup | One project per self-hoster with two OAuth consents: Inbox Zero's (write scopes) and Towpath's (`gmail.readonly`). Separate tokens, separate containers | To document |
| Model endpoint | Configuration | Inbox Zero accepts an OpenAI-compatible base URL; Towpath's setup points it at an endpoint the owner chooses | To verify |
| Inbox Zero API | Towpath reads | Rules, senders and unsubscribe status, statistics for Towpath's overview | To verify |
| Inbox Zero webhook action | Inbox Zero calls Towpath | A rule can notify Towpath, for example "receipt with a PDF arrived", to trigger a scan | To verify |
| Links | Towpath to Inbox Zero | Open Inbox Zero's own screens for detailed work | To build |

## Caveats

- **Automatic actions.** Inbox Zero's rules run automatically and can label, archive, draft, reply, forward, send, or delete, depending on how the person configures them. Towpath's approve-before-act model does not apply inside Inbox Zero; its own settings do. The setup guide should recommend starting with rules limited to labeling, archiving, and drafting.
- **Model policy.** Towpath's endpoint grants and item-level model use ([model providers](model-providers.md), [life stream](life-stream.md#audience-and-model-use)) do not govern what Inbox Zero sends to its model. Pointing it at an endpoint the owner controls is how [D6](decisions.md#d6-remote-model-use) applies to it.
- **Its own copy of data.** Inbox Zero stores data about mail in its own database. That data belongs to Inbox Zero's service, not to Towpath's stores, and is backed up or deleted with it.
- **License.** Inbox Zero is AGPL-3.0 with added terms that are free for personal use but restrict commercial use and organizations of five or more business users. Under the [license policy](integrations.md) it is an optional profile, labeled with those terms; Towpath's code does not depend on or copy it.
- **Fit must be confirmed.** Before relying on it, a hands-on evaluation on the owner's own account should check the points marked "to verify" above, which actions can be disabled, and how well its classification works with a local model.

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
