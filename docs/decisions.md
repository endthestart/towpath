# Recommendations and open decisions

Status: **mixed.** Items marked *Accepted* were agreed by the owner; everything else is a recommendation pending review. Each item says what it blocks so decisions can be made one at a time.

## Recommendations in this design

These are built into the current documents. The owner can accept or overturn each one.

| ID | Recommendation | Where | Blocks if overturned |
| --- | --- | --- | --- |
| R1 | One codebase, three process roles (`towpath` app, `towpath-connect`, optional `towpath-act`); modules enabled per deployment | [components](components.md#deployable-units) | First slice structure |
| R2 | One connector tier and source store shared by mail and life, with explicit per-consumer grants | [components](components.md#stores-and-ownership), [interfaces](interfaces.md#2-source-read-api-app-reads-the-source-store) | First slice |
| R3 | Occurrences per source with dated observations and match strengths; no merging | [interfaces](interfaces.md#occurrence) | First slice |
| R4 | Accepted claims capture cited excerpts; a person can also preserve the full item or attachment as a content-addressed artifact (owner request, 2026-10-01) | [preserved artifacts](scans-and-destinations.md#preserved-artifacts) | Life slice; future archive package |
| R5 | No mailbox write credential until D1 is decided; ship proposal review, checklist export, and generated filters first | [mailbox actions](mail-boundaries.md#recommendation) | Nothing now |
| R6 | Permanent deletion is never a Towpath action | [mailbox actions](mail-boundaries.md#action-tiers) | Nothing now |
| R7 | Endpoint profiles with destination class, data-class ceiling, ledger grants, capability probes, same-endpoint fallbacks only | [model providers](model-providers.md) | Slice 1b |
| R8 | Archive adapter reads standard mail files with an optional manifest (accepted as D4) | [archive adapter](archive-adapter.md#input-options) | Archive connector |
| R9 | First slice is synthetic, read-only, no models, no network | [first slice](first-slice.md) | Starting implementation |
| R10 | Generic scans (selectors), destination connectors with a read half in `towpath-connect` and a write half in `towpath-act`, and delivery as an approved action | [scans and destinations](scans-and-destinations.md) | Find-and-route features |

## Open owner decisions

### D1. Mailbox and destination execution

How much execution belongs in Towpath? Since destinations were added, this splits in two:

- **Deliveries to destinations** (add a file to a folder, document system, or media archive). Additive; they never change the mailbox. A folder destination needs no credential.
- **Mailbox changes** (labels, archive, filters, later trash). They change the account, and provider permissions for them are broad ([provider realities](mail-boundaries.md#provider-permission-realities)).

Options for mailbox changes are compared in [mailbox actions](mail-boundaries.md#options-for-mailbox-execution).

- **Recommended:** build `towpath-act` first for folder deliveries only, which exercises approvals, receipts, and the separate process without any write credential. Keep mailbox changes to review, checklist export, and generated filters (options A and E) until private use shows whether in-Towpath execution (C) or an external tool (D) is worth it.
- **Status:** owner unsure; to be asked separately with this context.
- **Blocks:** roadmap milestone 7 only.

### D2. Implementation language and storage

**Accepted 2026-10-01.** Python with SQLite, one file per store. The standard library handles RFC 5322 parsing, Maildir, and mbox; SQLite full-text search covers lexical search without a server.

### D3. First real mail source

**Accepted 2026-10-01.** The first real read-only connector uses the Gmail API with a read-only scope. It fits the owner's account and the index-then-fetch design: stable native IDs, real labels, and part structure without downloading attachments. The connector interface stays protocol-neutral so IMAP or JMAP can follow. Self-hosters must register their own OAuth client; the setup guide must cover that after the related [facts are verified](#facts-to-verify-before-implementation).

### D4. Archive input

**Accepted 2026-10-01.** Towpath reads the archive as standard mail files (Maildir or mbox) plus the optional [manifest](archive-adapter.md#draft-manifest-row), whatever archive tool the evacuation chooses. The evacuation can add a manifest writer if its tool does not produce one. A tool-specific plugin is not planned.

### D5. Content retention

**Accepted 2026-10-01.** Towpath gets read access to the whole account. It indexes every message's metadata and part structure, fetches bodies and attachments one item at a time when a rule, scan, or person needs them, and evicts cached content later. Durable copies exist only when a person preserves an item or part. Specific uses such as "find all photos" or "find PDFs not already in my document system" are built as generic [scans and destinations](scans-and-destinations.md), not as one-off features.

### D6. Remote model use

- **Question:** whether any remote endpoint (`self-hosted` other than this machine, or `third-party`) may receive `content` or `derived-personal` data in the owner's deployment, and whether the public default UI should even offer third-party grants for those classes.
- **Recommended:** allow `self-hosted` grants per endpoint; require a second confirmation for `third-party` content grants.
- **Blocks:** slice 1b UI wording; not the gateway design.

### D7. Coverage report home

**Accepted 2026-10-01.** Towpath produces the provider-versus-archive coverage report as an advisory, read-only report with match strengths, because it already indexes both sides. The report carries no authority. The evacuation may use it as one input but keeps its own per-message verification before any deletion. First-slice check 7 stays.

### D8. Unsubscribe handling

- **Options:** display only; Towpath performs one-click POST unsubscribes after per-sender approval; never.
- **Recommended:** display only until D1 is settled; one-click POST would be a tier 3 executor action.
- **Blocks:** nothing before milestone 7.

### D9. First non-email life source

- **Recommended:** typed recollections plus an iCalendar file import, so life summary is proven without any mail.
- **Blocks:** the life-summary slice (milestone 5).

### D10. Order after the first slice

**Accepted 2026-10-01.** A real read-only mail connector comes next, before life summary without email.

## Facts to verify before implementation

These affect the design but could not be confirmed from primary documentation while writing it. Verify against current provider documentation and record the date checked.

| Claim used in the design | Affects |
| --- | --- |
| Gmail's narrowest scope that can change labels or archive also permits reading and sending | Mailbox execution analysis |
| Creating Gmail filters needs a separate settings scope | Option E and executor tier 2 |
| A read-only Gmail scope exists that allows full message reads, and a metadata-only scope restricts search | First Gmail connector |
| Gmail can list a message's part structure, with attachment sizes and IDs, without downloading attachment bytes | Index-all, fetch-on-demand (D5) |
| OAuth clients in testing status issue refresh tokens with short lifetimes, and restricted scopes require verification for public distribution | Self-hosted setup guide |
| Gmail incremental-sync cursors can expire, requiring a full sync | Connector cursor handling |
| Some JMAP providers issue read-only API tokens | D3 |
| Provider exports may include labels in a message header | Archive adapter |
| One-click unsubscribe uses a `List-Unsubscribe-Post` header (RFC 8058) | Unsubscribe display and D8 |

## Decision log

| Date | Decision | Status |
| --- | --- | --- |
| 2026 (first public design) | Three distinct concerns; evacuation is independent of Towpath | Agreed |
| 2026 (first public design) | Configurable OpenAI-compatible endpoints; Poundlock optional; no implicit destination | Agreed |
| 2026 (first public design) | Towpath proposes mailbox changes; a separately permissioned component executes approved actions | Tentative; see D1 |
| 2026-10-01 | D2: Python and SQLite | Accepted |
| 2026-10-01 | D10: real read-only mail connector before life summary without email | Accepted |
| 2026-10-01 | D3: Gmail API, read-only scope, for the first real connector | Accepted |
| 2026-10-01 | D4: archive read as Maildir or mbox plus optional manifest, independent of archive tool | Accepted |
| 2026-10-01 | D7: Towpath produces an advisory coverage report; deletion verification stays in the evacuation | Accepted |
| 2026-10-01 | D5: read access to all mail, index everything, fetch per item on demand; generic scans and destinations | Accepted |
| 2026-10-01 | Preservation of the full item or attachment, not only excerpts, as a foundation for a later archive package | Accepted as a requirement; design in R4 |
