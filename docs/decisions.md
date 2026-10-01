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
| R7 | Two separate settings from the first release: audience (`owner`, `shareable`) decides who may see an item; model use (`follow-grants`, `local-only`, `excluded`) decides where it may be processed | [life stream](life-stream.md#audience-and-model-use) | First slice schema |
| R8 | Accepted claims capture cited excerpts; a person can also preserve a full item or attachment (owner request) | [preserved artifacts](scans-and-destinations.md#preserved-artifacts) | Life stream |
| R9 | Scans with selectors; destinations as existing tools with a read half in `towpath-connect` and a write half in `towpath-act` | [scans and destinations](scans-and-destinations.md) | Attachment routing |
| R10 | Model endpoints with destination class, data-class grants, capability probes, and same-endpoint fallbacks only | [model providers](model-providers.md) | Slice 1b |
| R11 | No write credential before roadmap milestone 5; permanent deletion is never a Towpath action | [mail management](mail-management.md#action-tiers) | Nothing now |
| R12 | First slice is synthetic, read-only, no models, no network | [first slice](first-slice.md) | Starting implementation |
| R13 | License policy (accepted 2026-10-01): components must publish source and be free for personal use. Unrestricted open source can be required or bundled; components with personal-use terms stay optional, labeled, and never copied into Towpath | [integrations](integrations.md) | Every integration choice |
| R14 | Light first stack: Typer, standard-library `sqlite3` with SQL migrations, FTS5, the openai library with explicit endpoints only, Pydantic, jsonschema, json-repair | [tooling review](research/2026-10-tooling-review.md#light-python-foundation) | First slice |
| R15 | No bundled model gateway; Towpath's thin gateway enforces data policy, and any gateway can be registered as an ordinary endpoint | [tooling review](research/2026-10-tooling-review.md#model-endpoints) | Slice 1b |
| R16 | The action runner allows only specific Gmail calls (label changes and label definitions), because `gmail.modify` can also send | [mail management](mail-management.md#action-runner-contract) | Milestone 5 |
| R17 | First Gmail index is incremental, resumable, newest first, with progress, because new Cloud projects get lower quotas | [tooling review](research/2026-10-tooling-review.md#gmail-access) | Milestone 3 |
| R18 | Towpath writes Gmail filter XML itself, using gmailctl as the format reference | [integrations](integrations.md#mail) | Milestone 4 |
| R19 | Document and photo systems use separate read and write identities; presence checks use each destination's hash algorithm | [scans and destinations](scans-and-destinations.md#destination-connectors) | Milestones 4 and 5 |
| R20 | Dates as EDTF with computed bounds and a provenance field; people matched by deterministic keys, then splink, as proposals only | [life stream](life-stream.md#the-evidence-rule), [integrations](integrations.md#life-stream) | Milestone 6 |

## Decisions

### D1. Mailbox and destination execution

**Accepted 2026-10-01.** `towpath-act`, the separate action runner, executes approved deliveries to destinations and tier 1 mailbox changes: add or remove labels, archive, mark read or unread ([action tiers](mail-management.md#action-tiers)). Its allowlist is configured at the runner; it executes only frozen, human-approved proposals, rechecks each target, writes receipts and an inverse proposal, and has a per-batch cap and dry-run mode. Drafts are [D11](#d11-draft-replies), rules are [D12](#d12-smart-rules), unsubscribes are [D8](#d8-unsubscribe-handling), and trash needs its own decision. The Gmail credential this requires is broader than the allowed actions; that is accepted, with the runner's separation and allowlist as the control.

### D2. Implementation language and storage

**Accepted 2026-10-01.** Python, with SQLite files (one per store) for Towpath's own data. Keep it light: the first builds use the standard library and small libraries, with no web framework. See [D13](#d13-web-framework).

### D3. First real mail source

**Accepted 2026-10-01.** The Gmail API, through Google's official client libraries, with a read-only scope for `towpath-connect`. Rechecked under integrate-first: local Gmail sync and indexing tools were considered, but they keep a full local copy of the mailbox, which conflicts with fetching content only when needed and drifts toward mail migration, which is not Towpath's purpose. The connector interface stays protocol-neutral so IMAP or JMAP can follow.

### D4. Archive input

**Withdrawn 2026-10-01.** It existed only to serve the owner's separate Gmail migration. A local mail archive is an ordinary optional source with no special design ([integrations](integrations.md#sources)).

### D5. Content retention

**Accepted 2026-10-01.** Towpath gets read access to the whole mailbox. It indexes every message's metadata and part structure, fetches bodies and attachments one item at a time when a feature or person needs them, and evicts cached content later. Durable copies exist only when a person preserves an item. Specific uses such as "find PDFs not already in my document system" are built as generic [scans and destinations](scans-and-destinations.md).

### D6. Remote model use

**Accepted 2026-10-01.** Towpath supports third-party OpenAI-compatible endpoints for anyone who configures them, but personal data classes are off by default for every endpoint that is not `this-machine` or `bundled`. An owner grants particular data classes to a named endpoint. A server elsewhere on the owner's network, including Poundlock, counts as remote (`self-hosted`) for these grants. For the owner's own deployment, the initial plan is to grant only endpoints the owner controls. Item-level [model use](life-stream.md#audience-and-model-use) can narrow this further.

### D7. Coverage report home

**Withdrawn 2026-10-01.** A "Gmail versus archive" coverage report would serve only the separate migration. Towpath's coverage reports describe sync completeness for each source.

### D8. Unsubscribe handling

**Accepted 2026-10-01.** Towpath shows each sender's declared unsubscribe methods and their destinations (an HTTPS link, a one-click endpoint, or a mailto address) and the person acts. Towpath does not visit links or send unsubscribe requests in the first release. One-click unsubscribe ([RFC 8058](https://www.rfc-editor.org/rfc/rfc8058)) is an HTTPS POST, which an ordinary link cannot perform, so the UI says when a method cannot be completed by clicking and what the person's options are.

### D9. Life-stream sources after mail

**Accepted 2026-10-01.** Contacts first (they resolve who appears in mail), then calendars (dated evidence for planned events), then photo libraries and document systems as their integrations are ready. Each source stays independently usable.

### D10. Order

**Accepted 2026-10-01.** Mail management first, starting with a real read-only Gmail connector. The life stream follows, starting from mail, then contacts, photos, and documents. Its design still does not depend on mail.

### D11. Draft replies

**Accepted 2026-10-01.** Towpath generates editable reply text for the person to copy into Gmail. Creating drafts directly is deferred until its convenience justifies a credential that can also send: Google's `gmail.compose` scope includes sending ([Gmail scopes](https://developers.google.com/workspace/gmail/api/auth/scopes)).

### D12. Smart rules

**Accepted 2026-10-01.** Start with a Towpath digest for important-but-not-daily mail and a preview of each proposed rule, which the person creates in Gmail themselves. Add filter-file export only after a generated file has been tested through Gmail's [filter import](https://support.google.com/mail/answer/6579) flow. Creating filters through the API comes later, if at all: it needs a separate settings permission and changes future mail automatically.

### D13. Web framework

**Accepted direction 2026-10-01.** No web framework in the first builds; Towpath stays a light Python and SQLite CLI until the workflows work. When the login and review UI is built, Django is the leading candidate, with Towpath's own views for its workflows (Django's admin is for internal management only, per its [documentation](https://docs.djangoproject.com/en/stable/ref/contrib/admin/)). Confirm at that milestone.

### D14. Gmail access for other self-hosters

Research: [Gmail access](research/2026-10-tooling-review.md#gmail-access).

- **Settled by research:** Towpath never operates a shared OAuth client, because public use of restricted Gmail scopes needs Google verification and a yearly security assessment. Each self-hoster creates their own Google Cloud project and OAuth client, requests `gmail.readonly` for `towpath-connect`, and publishes the app to production unverified (clicking through Google's warning), since an app left in testing status loses its refresh token every 7 days. This follows the personal-use exception and the documented pattern of other self-hosted Gmail tools. The setup guide must walk through it.
- **Open question:** should Towpath also offer IMAP with a Google app password as an optional quick start? It needs no Cloud project and indexes quickly, but the password allows full access, including sending and deleting, and read-only behavior would rest on Towpath's code alone.
- **Recommended:** Gmail API only at first; consider the IMAP quick start after the Gmail API path works.
- **Blocks:** the setup guide for milestone 3.

### D15. Role of Inbox Zero

- **Finding:** under the [license policy](integrations.md) it qualifies as an optional component with personal-use terms. It covers most of Towpath's mail features, but requires write scopes, runs its rules automatically (including sending, forwarding, and deleting), and needs Postgres and Redis ([review](research/2026-10-tooling-review.md#mail-management)).
- **Options:** design reference only; an optional Compose profile for people who accept its model; or deeper reuse.
- **Recommended:** design reference only, because running it would bypass Towpath's read-first, approve-before-act model.
- **Blocks:** nothing before milestone 4.

## Facts to verify before implementation

| Claim used in the design | Affects |
| --- | --- |
| Gmail's narrowest scope that can change labels or archive also permits reading and sending | Confirmed from Gmail's API discovery document (2026-10-01) |
| Creating Gmail drafts needs a scope that also permits sending (Google's scope documentation says `gmail.compose` includes sending) | D11 (accepted on that basis) |
| Creating Gmail filters needs a separate settings scope; Gmail imports filters from a file | Confirmed (discovery document; gmailctl documentation) |
| A read-only Gmail scope allows full message reads; a metadata-only scope restricts search | Confirmed; metadata-only also blocks part structure |
| Gmail lists a message's part structure, with attachment sizes and IDs, without downloading attachment bytes | Confirmed (`format=full`) |
| OAuth clients in testing status issue short-lived refresh tokens; exact personal-use exception conditions | Secondary sources agree (7 days; personal use by a few known users, under 100); confirm against Google's pages when writing the setup guide |
| Gmail incremental-sync cursors can expire, requiring a full sync | Confirmed (404 on an expired cursor) |
| One-click unsubscribe uses a `List-Unsubscribe-Post` header (RFC 8058) | D8 |
| SQLite write-ahead logging works across containers with read-only mounts on one host | Likely not without existing side files; see [components](components.md#stores-and-ownership) |
| Each candidate in [integrations](integrations.md) meets the evaluation checklist | Researched 2026-10-01; hands-on check of exact API calls still needed before adoption |
| Quotas for new Google Cloud projects make a first full index slow (hours for a large mailbox) | Secondary; measure during milestone 3 |

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
| 2026-10-01 | D6, D8, D9, D11, D12 | Accepted as recommended in owner review |
| 2026-10-01 | D13: no framework for now; Django leading candidate for the later UI | Accepted direction |
| 2026-10-01 | Audience and model use split into separate settings | Accepted (owner review) |
| 2026-10-01 | FOSS only for everything Towpath includes or bundles; Inbox Zero excluded (license adds commercial and enterprise restrictions) | Superseded the same day |
| 2026-10-01 | License policy: open source and free for personal use qualifies; personal-use-only components stay optional and labeled; Inbox Zero becomes a candidate again | Accepted |
| 2026-10-01 | Tooling review completed; recommendations R14 to R20 added; D14 partly settled; D15 opened | Research |
