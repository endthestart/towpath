# Recommendations and decisions

Status: **mixed.** Items marked *Accepted* were agreed by the owner. *Withdrawn* items no longer apply. Everything else is a recommendation or open question. Each item says what it blocks so decisions can be made one at a time.

## Recommendations in this design

These are built into the current documents. The owner can accept or overturn each one.

| ID | Recommendation | Where | Blocks if overturned |
| --- | --- | --- | --- |
| R1 | One Python codebase run as four Compose services (`towpath-web`, `towpath-worker`, `towpath-connect`, optional `towpath-act`), with bundled tools as optional profiles | [components](components.md#services) | First slice structure |
| R2 | Integrate first: connect to existing deployments, bundle existing tools, use libraries; write code only for what remains. Every integration stays a candidate until verified | [architecture](architecture.md#integrate-first), [integrations](integrations.md) | Everything |
| R3 | Connection setup runs in the service that will hold the credential; the web UI never sees tokens | [components](components.md#services) | First real connector |
| R4 | One connection per source, used by features through explicit grants | [components](components.md#permission-matrix) | First slice |
| R5 | Items recorded per source with dated observations; duplicates linked, never merged | [interfaces](interfaces.md#occurrence) | First slice |
| R6 | Human decisions kept in their own store, apart from rebuildable derived data | [components](components.md#stores-and-ownership) | First slice |
| R7 | Visibility classes (`private`, `personal`, `shareable`) from the first release; private items never reach models or exports | [life stream](life-stream.md#visibility-and-authorship) | First slice schema |
| R8 | Accepted claims capture cited excerpts; a person can also preserve a full item or attachment (owner request) | [preserved artifacts](scans-and-destinations.md#preserved-artifacts) | Life stream |
| R9 | Scans with selectors; destinations as existing tools with a read half in `towpath-connect` and a write half in `towpath-act` | [scans and destinations](scans-and-destinations.md) | Attachment routing |
| R10 | Model endpoints with destination class, data-class grants, capability probes, and same-endpoint fallbacks only | [model providers](model-providers.md) | Slice 1b |
| R11 | No write credential before roadmap milestone 5; permanent deletion is never a Towpath action | [mail management](mail-management.md#action-tiers) | Nothing now |
| R12 | First slice is synthetic, read-only, no models, no network | [first slice](first-slice.md) | Starting implementation |

## Decisions

### D1. Mailbox and destination execution

**Accepted 2026-10-01.** `towpath-act`, the separate action runner, executes approved deliveries to destinations and tier 1 mailbox changes: add or remove labels, archive, mark read or unread ([action tiers](mail-management.md#action-tiers)). Its allowlist is configured at the runner; it executes only frozen, human-approved proposals, rechecks each target, writes receipts and an inverse proposal, and has a per-batch cap and dry-run mode. Drafts are [D11](#d11-draft-replies), rules are [D12](#d12-smart-rules), unsubscribes are [D8](#d8-unsubscribe-handling), and trash needs its own decision. The Gmail credential this requires is broader than the allowed actions; that is accepted, with the runner's separation and allowlist as the control.

### D2. Implementation language and storage

**Accepted 2026-10-01.** Python, with SQLite files (one per store) for Towpath's own data. The web framework is [D13](#d13-web-framework).

### D3. First real mail source

**Accepted 2026-10-01.** The Gmail API, through Google's official client libraries, with a read-only scope for `towpath-connect`. Rechecked under integrate-first: local Gmail sync and indexing tools were considered, but they keep a full local copy of the mailbox, which conflicts with fetching content only when needed and drifts toward mail migration, which is not Towpath's purpose. The connector interface stays protocol-neutral so IMAP or JMAP can follow.

### D4. Archive input

**Withdrawn 2026-10-01.** It existed only to serve the owner's separate Gmail migration. A local mail archive is an ordinary optional source with no special design ([integrations](integrations.md#sources)).

### D5. Content retention

**Accepted 2026-10-01.** Towpath gets read access to the whole mailbox. It indexes every message's metadata and part structure, fetches bodies and attachments one item at a time when a feature or person needs them, and evicts cached content later. Durable copies exist only when a person preserves an item. Specific uses such as "find PDFs not already in my document system" are built as generic [scans and destinations](scans-and-destinations.md).

### D6. Remote model use

- **Question:** may a remote endpoint (`self-hosted` elsewhere, or `third-party`) receive message content or life-stream data in the owner's deployment, and should the public UI offer third-party grants for those classes at all?
- **Recommended:** allow `self-hosted` grants per endpoint; require a second confirmation for `third-party` content grants. `private` items never go anywhere.
- **Blocks:** slice 1b wording; not the gateway design.

### D7. Coverage report home

**Withdrawn 2026-10-01.** A "Gmail versus archive" coverage report would serve only the separate migration. Towpath's coverage reports describe sync completeness for each source.

### D8. Unsubscribe handling

- **Options:** show the sender's unsubscribe link and let the person act; Towpath performs one-click unsubscribes after per-sender approval; never.
- **Recommended:** show the link. One-click execution sends a request to the sender and confirms the address is live, which is outside D1's scope.
- **Blocks:** roadmap milestone 4 wording only.

### D9. Life-stream sources after mail

- **Recommended:** contacts first (people are the backbone), then read connections to the person's photo library and document system as evidence sources, with recollections available from the start of the life stream.
- **Blocks:** roadmap milestone 7 ordering.

### D10. Order

**Accepted 2026-10-01.** Mail management first, starting with a real read-only Gmail connector. The life stream follows, starting from mail, then contacts, photos, and documents. Its design still does not depend on mail.

### D11. Draft replies

- **Options:** (a) Towpath generates draft text that the person edits and copies; (b) after approval, `towpath-act` creates a draft in Gmail, never sending it.
- **Tradeoff:** (b) is smoother, but the Gmail permission for creating drafts appears also to allow sending (to verify), so the runner would hold a send-capable credential with sending excluded only by its allowlist.
- **Recommended:** (a) first; decide (b) after verifying the scope.
- **Blocks:** roadmap milestone 5 scope.

### D12. Smart rules

For mail that is important but does not need reading every day.

- **Options:** (a) generate a Gmail filter file the person imports; (b) `towpath-act` creates filters after approval, which needs a settings permission; (c) a Towpath digest page that summarizes that mail on a schedule and flags anything needing attention, with no mailbox change.
- **Recommended:** (c) plus (a) first; (b) later if importing filters proves tedious.
- **Blocks:** roadmap milestone 4 scope.

### D13. Web framework

- **Options:** Django (built-in authentication, admin, migrations, SQLite support); FastAPI with server-rendered pages (lighter, more assembly).
- **Recommended:** Django, because login, configuration screens, and review lists are most of the first UI, and Django supplies them.
- **Blocks:** first slice, if it includes the web UI; otherwise milestone 3.

## Facts to verify before implementation

| Claim used in the design | Affects |
| --- | --- |
| Gmail's narrowest scope that can change labels or archive also permits reading and sending | D1 analysis |
| Creating Gmail drafts needs a scope that also permits sending | D11 |
| Creating Gmail filters needs a separate settings scope; Gmail imports filters from a file | D12 |
| A read-only Gmail scope allows full message reads; a metadata-only scope restricts search | First Gmail connector |
| Gmail lists a message's part structure, with attachment sizes and IDs, without downloading attachment bytes | D5 |
| OAuth clients in testing status issue short-lived refresh tokens; restricted scopes require verification for public distribution | Self-hosted setup guide |
| Gmail incremental-sync cursors can expire, requiring a full sync | Connector cursor handling |
| One-click unsubscribe uses a `List-Unsubscribe-Post` header (RFC 8058) | D8 |
| SQLite write-ahead logging works across containers with read-only mounts on one host | Store layout ([components](components.md#stores-and-ownership)) |
| Each candidate in [integrations](integrations.md) meets the evaluation checklist | Any adoption |

## Decision log

| Date | Decision | Status |
| --- | --- | --- |
| 2026 (first public design) | Configurable OpenAI-compatible endpoints; Poundlock optional; no implicit destination | Agreed |
| 2026 (first public design) | Towpath proposes changes; a separately permissioned component executes approved actions | Confirmed by D1 |
| 2026-10-01 | D1: action runner executes deliveries and tier 1 mailbox changes, with safeguards | Accepted |
| 2026-10-01 | D2: Python and SQLite | Accepted |
| 2026-10-01 | D3: Gmail API, read-only, for the first connector | Accepted; rechecked under integrate-first |
| 2026-10-01 | D5: read access to all mail; index everything, fetch on demand; generic scans and destinations | Accepted |
| 2026-10-01 | D10: mail management first; life stream follows from mail | Accepted |
| 2026-10-01 | Preserve full items or attachments, not only excerpts | Accepted as a requirement (R8) |
| 2026-10-01 | Towpath is a self-hosted front end that integrates existing tools, deployed with Docker Compose, able to bundle tools or point at existing ones | Accepted direction (R1, R2) |
| 2026-10-01 | Moving mail out of Gmail is a separate personal project, outside Towpath | Accepted |
| 2026-10-01 | D4 and D7 | Withdrawn; they served only that separate project |
