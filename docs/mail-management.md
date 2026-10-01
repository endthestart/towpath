# Mail management

Status: **designed, not built.** Nothing in this repository accesses or changes a live mailbox.

Mail management is Towpath's first capability: a person connects a mailbox (Gmail first, [D3](decisions.md#d3-first-real-mail-source)) and works through suggestions that help them handle mail that stays at the provider. No single existing mail-management application covers this well enough to adopt wholesale, so Towpath builds the review experience and borrows ideas and libraries where they fit ([integrations](integrations.md#mail)).

## Features

| Feature | What the person sees | Signals used | Model needed? | Output |
| --- | --- | --- | --- | --- |
| Categorize | Mail grouped into people, lists and newsletters, receipts and transactions, notifications, promotions, travel, and custom categories | Headers (`List-Id`, `List-Unsubscribe`, `Precedence`), sender history, provider categories observed at sync, person's corrections | Optional, for ambiguous mail | Category labels proposed; Towpath-only tags immediately |
| Important and unanswered | Threads where a real person wrote to them, expects a reply, and has not had one | Direct addressing, sender is a known correspondent, earlier replies, question detection, age | Optional, for "does this need a reply?" | A review list; nothing changes in the mailbox |
| Unsubscribe review | Senders and lists ranked by volume and engagement, with the sender's declared unsubscribe methods | Volume, unread ratio, last opened or replied, `List-Unsubscribe` and one-click headers | No | Person unsubscribes via the presented link; or a label or filter proposal |
| Draft replies | A suggested reply for a chosen thread, editable in Towpath | Thread content, person's past replies to this correspondent (if granted) | Yes | Copy to clipboard, or a draft in the mailbox if [D11](decisions.md#d11-draft-replies) allows; never sent by Towpath |
| Smart rules | "Important but not daily" mail, such as statements or school updates, handled without inbox noise | Categories plus the person's corrections | Optional | A provider filter (generated, or created after approval) and/or a Towpath digest ([D12](decisions.md#d12-smart-rules)) |
| Route attachments | PDFs and photos found in mail, compared against the document system or photo library | Part structure, content hashes, destination contents | No | Delivery proposals ([scans and destinations](scans-and-destinations.md)) |
| Search | Full-text search across indexed and fetched mail | Index, cached content | No | Results with links to the message at the provider |

Corrections are first-class. When a person moves a message to another category or dismisses an "unanswered" item, the correction is stored in the decisions store and outranks rules and models on the next run.

## Action tiers

Actions differ in reversibility, in whether they affect future mail, and in whether they reach a third party.

| Tier | Examples | Reversible? | Effects outside the account | Home |
| --- | --- | --- | --- | --- |
| 0. Towpath only | Tags, notes, triage status, dismissals | Yes | None | `towpath-web`; no provider permission |
| 1. Message state | Add or remove a label, mark read, archive (leave the inbox) | Yes, by the inverse action | None | `towpath-act` ([D1](decisions.md#d1-mailbox-and-destination-execution), accepted) |
| 1d. Draft | Create a draft reply in the mailbox | Yes, delete the draft | None until a person sends it themselves | Open: [D11](decisions.md#d11-draft-replies) |
| 2. Standing rules | Create a filter or server-side rule | Yes, but it silently affects future mail | None | Generated for the person to install; creation by `towpath-act` is open in [D12](decisions.md#d12-smart-rules) |
| 3. External requests | Unsubscribe by one-click POST or by sending mail | No | Confirms to the sender that the address is live | Person acts on the presented link ([D8](decisions.md#d8-unsubscribe-handling)) |
| 4. Trash | Move to Trash | Within the provider's retention window | None | Needs its own decision |
| 5. Permanent deletion | Delete bypassing Trash | No | None | Never a Towpath action |

## Provider permission realities

A narrow provider credential is only partly possible, so the boundary is built from Towpath's own service and data controls.

- **Gmail API.** The scope needed to apply labels or archive appears also to allow reading and sending; creating drafts appears to need a scope that also allows sending; filters need a separate settings scope. Reading can use a read-only scope. Each self-hoster registers their own OAuth client, and unverified clients face consent-screen and token-lifetime limits. All of this is on the [facts to verify](decisions.md#facts-to-verify-before-implementation) list.
- **IMAP with an app password.** All-or-nothing: the password can read, change, and delete everything.
- **JMAP providers.** Some issue read-only API tokens, which suits reading. Write tokens are still broad within mail.

So the action runner's narrowness comes from: being the only service with the write token; an action allowlist configured at the runner, not in the web or worker; acting only on frozen, human-approved proposals; and rechecking provider state per item.

## Options for executing mailbox changes

| Option | Description | Boundary strength | Usefulness | Cost | Main risk |
| --- | --- | --- | --- | --- | --- |
| A. Review only | Approved proposals export as a checklist or search query for manual use | Strongest; no write credential | Low for bulk work | Lowest | Tedious; people grant broad access to another tool instead |
| B. Writes in web or worker | The service that handles untrusted content also holds the write credential | Weakest | High | Low | Prompt injection or a bug reaches a write path |
| C. Separate action runner | `towpath-act` with its own credential, allowlist, recheck, and receipts | Strong | High | Medium | Runner bugs; broad provider scopes |
| D. External executor | Another tool consumes Towpath's approved proposals | Strong for Towpath | Depends on the tool | Split across projects | Proposal format becomes a public contract early |
| E. Provider-native rules | Towpath generates filters the person installs; the provider applies them | Strong for Towpath | High for recurring mail; none for backlog | Low | Rules act silently on future mail |

**Decision ([D1](decisions.md#d1-mailbox-and-destination-execution), accepted):** C for tier 1 changes and for deliveries, plus E for rules. Until the action runner exists (roadmap milestone 5), mail management works in A plus E mode with no write credential anywhere.

## Action runner contract

- Inputs: approval records with their frozen proposals, and the runner's own allowlist. It ignores classifications, model output, and life-stream data.
- Before acting: verify the proposal digest; refuse action types not allowlisted and expired proposals; recheck each target with the provider.
- While acting: rate-limit; stop the batch on an unexpected error class; never retry a non-idempotent action without a fresh recheck.
- After acting: write a receipt per item; generate an inverse proposal for undo; return changed or failed items for review.
- Safeguards for mailbox changes: per-batch cap and a dry-run mode that rechecks without acting.
- Never: permanent deletion, sending mail, or changing account settings outside an allowlisted rule type.

## Prompt injection and model output

Mail is untrusted input. Model endpoints receive no tools. A model can suggest a category, a reason, or draft text, and the result records that it was model-produced. A model cannot approve, change an allowlist, or reach the action runner. Review screens show exact targets and observed state, not only a model summary. Draft text is shown in full before any draft is created.

## Example

1. Towpath finds 40 recurring newsletters. It ranks them by volume and engagement and shows each one's unsubscribe link. The person unsubscribes from 12 by following the links, and approves a "Newsletters, skip inbox" filter for the rest.
2. It finds 6 threads from known correspondents asking questions, with no reply. The person drafts replies for two in Towpath and copies them into Gmail (or, if D11 allows, approves creating Gmail drafts).
3. A scan finds 30 PDF statements; 22 are already in the document system. The person approves delivering the other 8, and `towpath-act` records each new document ID.
