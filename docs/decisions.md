# Recommendations and open decisions

Status: **recommendations pending owner review.** Nothing here is final until marked accepted. Each item says what it blocks so decisions can be made one at a time.

## Recommendations in this design

These are built into the current documents. The owner can accept or overturn each one.

| ID | Recommendation | Where | Blocks if overturned |
| --- | --- | --- | --- |
| R1 | One codebase, three process roles (`towpath` app, `towpath-connect`, optional `towpath-act`); modules enabled per deployment | [components](components.md#deployable-units) | First slice structure |
| R2 | One connector tier and source store shared by mail and life, with explicit per-consumer grants | [components](components.md#stores-and-ownership), [interfaces](interfaces.md#2-source-read-api-app-reads-the-source-store) | First slice |
| R3 | Occurrences per source with dated observations and match strengths; no merging | [interfaces](interfaces.md#occurrence) | First slice |
| R4 | Accepted claims capture cited excerpts so citations survive mailbox changes and evacuation | [architecture](architecture.md#design-rules) | Life slice |
| R5 | No provider write credential until a separate decision; ship proposal review, checklist export, and generated filters first | [mailbox actions](mail-boundaries.md#recommendation) | Nothing now |
| R6 | Permanent deletion is never a Towpath action | [mailbox actions](mail-boundaries.md#action-tiers) | Nothing now |
| R7 | Endpoint profiles with destination class, data-class ceiling, ledger grants, capability probes, same-endpoint fallbacks only | [model providers](model-providers.md) | Slice 1b |
| R8 | Archive adapter reads standard mail files first, with an optional manifest | [archive adapter](archive-adapter.md#input-options) | Archive connector |
| R9 | First slice is synthetic, read-only, no models, no network | [first slice](first-slice.md) | Starting implementation |

## Open owner decisions

### D1. Mailbox execution

How much mailbox execution belongs in Towpath? Options are compared in [mailbox actions](mail-boundaries.md#options-for-mailbox-execution).

- **Recommended:** none for now (options A and E), then decide between an internal executor (C) and an external one (D) for tier 1 actions after using proposal review on real mail privately.
- **Blocks:** milestone 6 only. Nothing earlier needs it.

### D2. Implementation language and storage

- **Recommended:** Python with SQLite (one file per store) for the first slice. Python's standard library handles RFC 5322 parsing, Maildir, and mbox; SQLite with full-text search covers lexical search without a server. The ignore rules already anticipate a Python layout.
- **Alternatives:** Go or Rust for single-binary distribution; a database server instead of SQLite files.
- **Blocks:** starting the first slice.

### D3. First real mail source

- **Options:** Gmail API with a read-only scope (matches the owner's account; richest labels and IDs; OAuth client registration friction for self-hosters); IMAP (generic; app-password access is all-or-nothing); JMAP (cleaner API and read-only tokens on some providers; smaller user base).
- **Recommended:** Gmail API read-only first if the owner's own use is the priority; IMAP first if broad usefulness is.
- **Blocks:** roadmap milestone 4.

### D4. Archive input

- **Question:** which archive tool and format the evacuation chooses, and whether it can write the [manifest](archive-adapter.md#draft-manifest-row).
- **Recommended:** keep Towpath on Maildir or mbox plus manifest regardless of tool.
- **Blocks:** roadmap milestone 7; not the synthetic Maildir connector.

### D5. Content retention

How much provider mail content does Towpath keep locally when mail stays at the provider?

- **Options:** metadata only; on-demand bodies with eviction; full content cache.
- **Recommended:** on-demand bodies by default, plus permanent capture of excerpts cited by accepted claims. A full cache makes Towpath a partial archive with its own backup and privacy duties.
- **Blocks:** roadmap milestone 4.

### D6. Remote model use

- **Question:** whether any remote endpoint (`self-hosted` other than this machine, or `third-party`) may receive `content` or `derived-personal` data in the owner's deployment, and whether the public default UI should even offer third-party grants for those classes.
- **Recommended:** allow `self-hosted` grants per endpoint; require a second confirmation for `third-party` content grants.
- **Blocks:** slice 1b UI wording; not the gateway design.

### D7. Coverage report home

- **Question:** should the provider-versus-archive coverage report live in Towpath, in the evacuation tooling, or both?
- **Recommended:** Towpath produces it as an advisory read-only report because it already has both sources' occurrences; the evacuation keeps its own per-message verification for any deletion.
- **Blocks:** first slice check 7 (can be dropped if the answer is "evacuation only").

### D8. Unsubscribe handling

- **Options:** display only; Towpath performs one-click POST unsubscribes after per-sender approval; never.
- **Recommended:** display only until D1 is settled; one-click POST would be a tier 3 executor action.
- **Blocks:** nothing before milestone 6.

### D9. First non-email life source

- **Recommended:** typed recollections plus an iCalendar file import, so life summary is proven without any mail.
- **Blocks:** the life-summary slice.

### D10. Order after the first slice

- **Options:** real mail connector first, or life summary without email first.
- **Recommended:** life summary without email first if the goal is to prove independence early; real mail first if daily mail management is the more urgent personal need.

## Facts to verify before implementation

These affect the design but could not be confirmed from primary documentation while writing it. Verify against current provider documentation and record the date checked.

| Claim used in the design | Affects |
| --- | --- |
| Gmail's narrowest scope that can change labels or archive also permits reading and sending | Executor permission analysis |
| Creating Gmail filters needs a separate settings scope | Option E and executor tier 2 |
| A read-only Gmail scope exists that allows full message reads, and a metadata-only scope restricts search | First Gmail connector |
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
