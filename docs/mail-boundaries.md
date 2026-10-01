# Mailbox actions and authority

Status: **designed, not built, and not settled.** The owner tentatively agrees that Towpath proposes mailbox changes for review and that a separately permissioned component executes approved actions. How much execution belongs in Towpath is an [open decision](decisions.md#d1-mailbox-and-destination-execution). Nothing in this repository accesses or changes a live mailbox.

"Mailbox authority" means permission to change what is in an account at Gmail, Fastmail, or another provider.

## Action tiers

Actions differ in reversibility, in whether they affect future mail, and in whether they reach a third party.

| Tier | Examples | Reversible? | Side effects outside the account | Proposed home |
| --- | --- | --- | --- | --- |
| 0. Local only | Towpath tags, notes, triage status | Yes | None | App; no provider permission |
| 1. Message state | Add or remove a label, mark read, archive (leave the inbox) | Yes, by the inverse action | None | Executor, if built |
| 2. Standing rules | Create a filter or server-side rule | Yes, but it silently affects future mail | None | Generate for the person to install; executor later at most |
| 3. External requests | Unsubscribe by one-click POST or by sending mail | No | Confirms to the sender that the address is live | Present the link and the sender's stated method; person acts |
| 4. Trash | Move to Trash | Within the provider's retention window | None | Deferred; needs a separate decision |
| 5. Permanent deletion | Delete bypassing Trash; empty Trash | No | None | Never in Towpath. Belongs to the evacuation project's own verified process |

## Provider permission realities

The earlier design assumed the executor could hold a narrow provider credential. That is only partly possible, so the boundary must be built from Towpath's own process and data controls.

- **Gmail API.** The scope needed to apply labels or archive also appears to allow reading and sending mail; filters need a separate settings scope. A read connector can use a read-only scope. Self-hosted use usually means each person registers their own OAuth client, and unverified clients face consent-screen and token-lifetime limits. These points are recorded as [facts to verify](decisions.md#facts-to-verify-before-implementation) before implementation.
- **IMAP with an app password.** All-or-nothing: the password can read, change, and delete everything. Towpath cannot reduce that.
- **JMAP providers (for example Fastmail).** Some issue API tokens that can be limited to read-only access, which suits the connector. Write tokens are still broad within mail.

Consequence: even with the best available scope, the executor's narrowness comes from (a) being a separate process that alone holds the write credential, (b) an action-type allowlist configured at the executor rather than in the app, (c) acting only on digest-frozen, human-approved proposals, and (d) rechecking provider state per item.

## Options for mailbox execution

| Option | Description | Boundary strength | Usefulness | Build and maintenance | Main risk |
| --- | --- | --- | --- | --- | --- |
| A. Proposal only | Towpath never writes. Approved proposals export as a checklist, search query, or provider filter file for manual use | Strongest; no write credential exists | Low for bulk work; fine for small batches and rules | Lowest | People do bulk changes by hand or grant broad access to another tool |
| B. In-app writes | The app holds a write credential and acts directly | Weakest; untrusted-content parsing and model calls share a process with write authority | High | Low | Prompt injection or a bug can reach a write path |
| C. Internal executor | `towpath-act`, a separate process in this codebase with its own credential, allowlist, recheck, and receipts | Strong if deployed at enforcement level 3 ([components](components.md#enforcement-levels)) | High | Medium; provider APIs, rate limits, and reconciliation | Executor bugs; coarse provider scopes |
| D. External executor | A separate project (possibly the owner's migration tooling) consumes Towpath's proposal and approval files | Strong; Towpath stays read-only | High for whoever runs it | Towpath low; total effort higher across two projects | Two release cycles; the proposal format becomes a public contract early |
| E. Provider-native rules | Towpath generates filters or server rules the person installs; the provider applies them to future mail | Strong for Towpath; the provider acts | High for recurring mail; none for backlog | Low | Rules act silently on future mail; syntax differs per provider |

Deliveries of files to other systems ([destinations](scans-and-destinations.md)) use the same proposal, approval, and receipt pattern but never change the mailbox, so they can be decided separately from mailbox writes.

These options combine. A, D, and E share the same proposal and approval records, so choosing A first does not block C or D later.

## Recommendation

1. **Releases up to the first real connector: A plus E.** Proposal review, manual-action export, and generated filter files. No write credential exists anywhere in Towpath.
2. **Then decide between C and D for tier 1 only,** using evidence from real (private) use of A: how many approved actions people actually execute and how painful manual execution is.
3. **If C is chosen:** tier 1 only at first; executor runs at enforcement level 3; per-batch cap; dry-run mode that rechecks preconditions without acting; an inverse proposal generated for every executed batch so it can be undone.
4. **Tier 3 unsubscribes stay person-driven.** Towpath shows the sender's declared unsubscribe method and flags risky ones (unknown domains, mailto that would send from the account).
5. **Tier 4 needs its own decision. Tier 5 never belongs to Towpath.**

The tradeoff: A plus E delays bulk convenience in exchange for shipping useful mail management with no write authority at all. C gives the best experience but makes Towpath responsible for code that changes accounts.

## Executor contract (if option C is chosen)

- Inputs: approval records, their proposals, and the executor's own allowlist. It ignores classifications, model output, and life data.
- Before acting: recompute the proposal digest; refuse any action type not allowlisted; refuse expired proposals; recheck each target's precondition with the provider.
- While acting: rate-limit; stop the batch on an unexpected provider error class; never retry a non-idempotent action without a fresh recheck.
- After acting: write a receipt per item; generate the inverse proposal; return changed or failed items for review.
- Never: permanent deletion, sending mail, changing account settings outside an allowlisted rule type.

## Prompt injection and model output

Mail is untrusted input. Model endpoints receive no tools. A model can contribute to a proposal's reason and uncertainty, and the proposal records that it was model-produced. A model cannot approve, change an allowlist, or address the executor. The review screen shows exact targets and the observed state, not just a model summary.

## The personal Gmail evacuation

Preserving the owner's historical Gmail in a local living archive, and possibly removing provider copies, is an independent migration project. It chooses its archive tool, backups, restore tests, and deletion schedule. It is not a Towpath workflow or default.

Towpath relates to it in two ways only:

- Towpath can read the resulting archive through the [archive adapter](archive-adapter.md).
- Towpath can produce an advisory coverage report comparing a provider source with an archive source. The migration may use that report as one input, but it carries no deletion authority, and Towpath's executor (if any) never deletes because an archive copy exists.

## Example flow

1. Towpath finds 40 recurring newsletters. It proposes labels and generates a filter file. The person approves 32 label proposals and exports them as a checklist (option A) or, if an executor exists, sends them to `towpath-act`, which records receipts.
2. Towpath finds 8,000 old promotions and marks them low value. It does not propose deletion. If an archive source is configured, the coverage report says how many have strong archive matches. What to do with provider copies is the evacuation project's decision.
3. Towpath sees a travel confirmation in a mailbox that has a life grant. The life module proposes a *plan* claim. Accepting it captures the citation. That acceptance grants no mailbox permission.
