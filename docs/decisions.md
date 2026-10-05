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
| R11 | Towpath's own services hold no mailbox write credential; mailbox changes belong to the integrated mail-management tool; permanent deletion is never a Towpath action | [mail management](mail-management.md#approach) | Nothing now |
| R12 | First slice is synthetic, read-only, no models, no network | [first slice](first-slice.md) | Starting implementation |
| R13 | License policy (accepted 2026-10-01): components must publish source and be free for personal self-hosting. Open source can be required or bundled; *source available with use restrictions* stays optional, labeled, and never copied into Towpath | [integrations](integrations.md) | Every integration choice |
| R14 | **Accepted 2026-10-01.** Light first stack: Typer, standard-library `sqlite3` with SQL migrations, FTS5, the openai library with explicit endpoints only, Pydantic, jsonschema, json-repair. Domain logic stays independent of any future web framework | [tooling review](research/2026-10-tooling-review.md#light-python-foundation) | First slice |
| R15 | **Accepted 2026-10-01.** No bundled model gateway; Towpath's own permission checks run before every model call, and any external gateway is an optional ordinary endpoint | [tooling review](research/2026-10-tooling-review.md#model-endpoints) | Slice 1b |
| R16 | If Towpath ever holds a Gmail write token, its action runner allows only specific calls, because `gmail.modify` can also send | [mail management](mail-management.md#if-towpath-builds-its-own-features-later) | Only if Towpath builds mailbox actions |
| R17 | **Accepted 2026-10-01.** First Gmail index is incremental, resumable, newest first, with progress. Google confirms lower quotas for new projects; the five-to-six-hour figure for 100,000 messages is arithmetic, not a measurement | [tooling review](research/2026-10-tooling-review.md#gmail-access) | Milestone 4; implemented with persistent pacing (2026-10-02), live validation pending |
| R18 | If Towpath builds rules itself, it writes Gmail filter XML using gmailctl as the format reference | [integrations](integrations.md#mail) | Only if Towpath builds rules |
| R19 | **Accepted 2026-10-01.** Document and photo systems use separate permission-limited users or keys, as each tool supports; presence checks use each destination's checksum, and Towpath keeps its own SHA-256 alongside | [scans and destinations](scans-and-destinations.md#destination-connectors) | Milestone 5 |
| R20 | **Accepted 2026-10-01.** Dates as EDTF with computed bounds, keeping the original date wording and its provenance; people matched by deterministic keys, then splink, as proposals only | [life stream](life-stream.md#the-evidence-rule), [integrations](integrations.md#life-stream) | Milestone 6 |
| R21 | An action runner must re-check presence at the destination immediately before delivering; a presence result is an observation at a time, not a guarantee (finding from the first slice) | [scans and destinations](scans-and-destinations.md#destination-connectors) | Milestone 5 action runner |

## Decisions

### D1. Mailbox and destination execution

**Revised 2026-10-01 (with D15).** Mail management needs write access; the life stream does not. Mailbox changes (labels, archive, unsubscribe, drafts, rules) are carried out by the integrated mail-management tool under its own settings, starting with Inbox Zero ([mail management](mail-management.md)). `towpath-act` executes approved deliveries to destinations such as a document system or photo library, under the [action runner contract](mail-management.md#if-towpath-builds-its-own-features-later). If Towpath later builds its own mailbox actions, they go through `towpath-act` with a call allowlist ([R16](#recommendations-in-this-design)). Permanent deletion is never a Towpath action.

*Earlier version (accepted the same day):* `towpath-act` would also execute tier 1 mailbox changes itself.

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

*Scope after D15:* applies to any unsubscribe feature Towpath builds itself. When Inbox Zero provides unsubscribing, its behavior applies.

**Accepted 2026-10-01.** Towpath shows each sender's declared unsubscribe methods and their destinations (an HTTPS link, a one-click endpoint, or a mailto address) and the person acts. Towpath does not visit links or send unsubscribe requests in the first release. One-click unsubscribe ([RFC 8058](https://www.rfc-editor.org/rfc/rfc8058)) is an HTTPS POST, which an ordinary link cannot perform, so the UI says when a method cannot be completed by clicking and what the person's options are.

### D9. Life-stream sources after mail

**Accepted 2026-10-01.** Contacts first (they resolve who appears in mail), then calendars (dated evidence for planned events), then photo libraries and document systems as their integrations are ready. Each source stays independently usable.

### D10. Order

**Accepted 2026-10-01.** Mail management first, starting with a real read-only Gmail connector. The life stream follows, starting from mail, then contacts, photos, and documents. Its design still does not depend on mail.

### D11. Draft replies

*Scope after D15:* applies to drafting Towpath builds itself. Inbox Zero creates drafts in Gmail.

**Accepted 2026-10-01.** Towpath generates editable reply text for the person to copy into Gmail. Creating drafts directly is deferred until its convenience justifies a credential that can also send: Google's `gmail.compose` scope includes sending ([Gmail scopes](https://developers.google.com/workspace/gmail/api/auth/scopes)).

### D12. Smart rules

*Scope after D15:* applies to rules Towpath builds itself. Inbox Zero provides its own rules and digest.

**Accepted 2026-10-01.** Start with a Towpath digest for important-but-not-daily mail and a preview of each proposed rule, which the person creates in Gmail themselves. Add filter-file export only after a generated file has been tested through Gmail's [filter import](https://support.google.com/mail/answer/6579) flow. Creating filters through the API comes later, if at all: it needs a separate settings permission and changes future mail automatically.

### D13. Web framework

**Accepted direction 2026-10-01.** No web framework in the first builds; Towpath stays a light Python and SQLite CLI until the workflows work. When the login and review UI is built, Django is the leading candidate, with Towpath's own views for its workflows (Django's admin is for internal management only, per its [documentation](https://docs.djangoproject.com/en/stable/ref/contrib/admin/)). Confirm at that milestone.

**Local preview 2026-10-05.** The optional read-only email interface uses Django, with custom views over the source store's read-only role. It has no ORM, admin, authentication, or connector credential path. The CLI remains usable without Django. This establishes the framework for the local preview; the authenticated login and review UI remains a later milestone. See [local UI](setup/local-ui.md).

### D14. Gmail access for other self-hosters

**Accepted 2026-10-01.** Gmail API only for the first release; no IMAP app-password option. App passwords add little here, since Inbox Zero still needs Google OAuth, and they are not available for every account configuration, so they cannot be a universal quick start ([Google's requirements](https://support.google.com/accounts/answer/185833)).

- **Initial approach (a product choice, not a permanent rule):** each self-hoster brings their own Google OAuth client, publishes it to production unverified, and uses it only for their own accounts, under Google's [personal-use exception](https://developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification). An app left in testing status loses its refresh token after 7 days ([Google OAuth](https://developers.google.com/identity/protocols/oauth2)). A verified, shared Towpath client remains an option for later if the project can meet verification and assessment requirements.
- **Separation:** Towpath and Inbox Zero use separate Google Cloud projects, because consent belongs to a project and incremental authorization can combine grants across clients in one project ([cross-client authorization](https://developers.google.com/identity/protocols/oauth2/cross-client-identity)). Towpath checks the scopes actually granted and refuses anything beyond `gmail.readonly`.
- **Blocks:** the setup guides for milestones 3 and 4.

### D15. Role of Inbox Zero

**Accepted 2026-10-01; operational fit pending.** If an existing tool meets the need now and works well, Towpath uses it. Inbox Zero is Towpath's first mail-management provider: an optional Compose profile (or an existing instance) that Towpath integrates with through the [provider contract](interfaces.md#6-mail-management-provider), holding its own Gmail write access. Alternatives can be added behind the same contract, and Towpath may later build its own features and replace it. Its public API covers statistics and rules; other features are links to its own screens. Caveats are in [mail management](mail-management.md#caveats). A focused [evaluation](evaluations/inbox-zero-plan.md) on a dedicated test mailbox comes before use with a primary account.

### D16. Gmail structure without content

- **Finding:** `format=full` returns parsed body content, and a MIME part can carry its bytes inline instead of an attachment ID ([formats](https://developers.google.com/workspace/gmail/api/reference/rest/v1/Format), [message part bodies](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages.attachments)). "Index broadly, fetch narrowly" is therefore not automatic.
- **Approach:** request a [partial response](https://developers.google.com/workspace/gmail/api/guides/performance) whose field mask names part IDs, types, file names, headers, sizes, and attachment IDs at each nesting level, and never body data. Tests with nested and inline-content fixtures must show that no body data is returned at any depth. If Gmail cannot exclude it reliably, inline data that arrives is discarded without caching, and the bandwidth cost is documented.
- **Status:** tooling built (`towpath connect verify-structure`); awaiting a run against real Gmail ([local quickstart](setup/local-quickstart.md#part-c-authorize-and-prove-the-read-only-boundary)).
- **Blocks:** relying on milestone 4 with a primary account.

### D17. File discovery boundary

**Accepted 2026-10-03 as the foundation's boundary; no provider adopted.** [File discovery](file-discovery.md) is an optional module (`towpath.discovery`, `towpath files ...`). It is useful without mail, models, or the life stream.
- **Ownership:**
  - An existing search tool owns crawling, parsing, unpacking, and the text index.
  - Towpath keeps only references, versions, coverage, citations, and recoveries, in a separate rebuildable `files.db` written by the connect role.
  - Grants per root and feature stay in the decisions store.
- **Separation:**
  - File discovery shares canonical IDs, credential references, store roles, and the evidence-reference shape.
  - It shares no code path with mail sync: no mail fields, no imports in either direction, and no change to `connect.sync`.
  - Disabling it leaves mail unchanged.
- **Providers:**
  - Recoll and sist2 are swappable candidates behind one contract. Each is GPL and runs as a separate program; Towpath copies none of its code.
  - Recoll gets the first adapter because its Python binding documents search, excerpt, and nested recovery, all verified natively on synthetic files ([evidence](evaluations/file-discovery-providers.md)).
  - sist2 has a capability slot until a stable excerpt and recovery interface is verified. Its raw index schema is documented as unstable.
  - AnythingLLM is out of scope for now.
- **Limits:** no source writes, moves, deletes, or deduplication; no model calls; no automatic claim acceptance.
- **Pending:** live evaluation of both tools on the owner's archives, and the choice between them. Both are local work ([handoff](setup/file-discovery-handoff.md)).

### D18. Container build and release path

**Accepted 2026-10-04 (owner requirement).** GitHub Actions tests the code, builds the container images, tests the built images, and publishes the tested images to GHCR. Arcane, or any deployment, only pulls an image by digest and runs it ([containers](setup/containers.md)).
- **No building on the server.** Deployment Compose files have no `build:`. There is no self-hosted runner on the owner's network, and CI holds no Arcane secret.
- **What publishes:** only pushes to `main`, `v*` tags, or a manual run with "publish" selected. It uses `GITHUB_TOKEN` with `packages: write` in that one job, and Actions are pinned by commit.
- **Same image:** the published image is the tested one, loaded from the test job and checked by ID, never rebuilt.
- **Tags:** revision tags are `sha-<commit>`, plus `vX.Y.Z` for releases. Existing tags are never overwritten, and there is no `latest`.
- **Images:**
  - `towpath`: the CLI, no Gmail or model extras.
  - `towpath-recoll`: adds Recoll 1.36.1, its binding, and document helpers, with license notices.

  Both run unprivileged and start no service, mail sync, or scan.
- **Deployment stays manual and local.** Pull, deploy, verify, and roll back are steps the owner runs.
- **Revised 2026-10-04 after release review:**
  - **Canonical image:** one per revision and target, `sha-<commit>`, built with ref-independent arguments.
  - **Release tags:** aliases of the canonical manifest (same digest). An existing canonical image is promoted only after its labels, its source artifact, and the GitHub run that built and tested it are verified.
  - **Corresponding source:** the exact source of every distribution package in each image is fetched, verified against `.dsc` checksums and the image's package list, and published to GHCR (`towpath-sources:sha256-<image config>`) before the image. Missing or mismatched source stops publication.
  - **Source retention (owner approved 2026-10-04):** published corresponding-source artifacts are retained indefinitely, including after matching images are retired. They have no expiry and are excluded from automated cleanup.
  - **Core image:** drops CPython's readline, gdbm, and Berkeley DB modules.

## Facts to verify before implementation

| Claim used in the design | Affects |
| --- | --- |
| Gmail's narrowest scope that can change labels or archive also permits reading and sending | Confirmed from Gmail's API discovery document (2026-10-01) |
| Creating Gmail drafts needs a scope that also permits sending (Google's scope documentation says `gmail.compose` includes sending) | D11 (accepted on that basis) |
| Creating Gmail filters needs a separate settings scope; Gmail imports filters from a file | Confirmed (discovery document; gmailctl documentation) |
| A read-only Gmail scope allows full message reads; a metadata-only scope restricts search | Confirmed; metadata-only also blocks part structure |
| Gmail lists a message's part structure, with attachment sizes and IDs, without downloading attachment bytes | **Corrected:** `format=full` includes parsed body content and inline part bytes; exclusion depends on a field mask that must be proven ([D16](#d16-gmail-structure-without-content)) |
| OAuth clients in testing status issue short-lived refresh tokens; exact personal-use exception conditions | Confirmed by Google's documentation (7-day expiry in testing; limited personal use without verification) |
| Gmail incremental-sync cursors can expire, requiring a full sync | Confirmed (404 on an expired cursor) |
| One-click unsubscribe uses a `List-Unsubscribe-Post` header (RFC 8058) | D8 |
| SQLite write-ahead logging works across containers with read-only mounts on one host | Likely not without existing side files; see [components](components.md#stores-and-ownership) |
| Each candidate in [integrations](integrations.md) meets the evaluation checklist | Researched 2026-10-01; hands-on check of exact API calls still needed before adoption |
| Quotas for new Google Cloud projects make a first full index slow (hours for a large mailbox) | Quota change confirmed by Google; duration to be measured during milestone 4 |

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
| 2026-10-01 | D15: Inbox Zero integrated as an optional Compose service for mail management; D1 revised so `towpath-act` handles deliveries; D8, D11, D12 scoped to Towpath-built features | Accepted |
| 2026-10-01 | Owner review: D14 accepted (Gmail API only; bring-your-own OAuth as the initial approach; separate Cloud projects); R14, R15, R17, R19, R20 accepted with qualifications; D16 opened; Inbox Zero provider contract and evaluation plan added; license class renamed "source available with use restrictions" | Accepted |
| 2026-10-01 | First slice built on synthetic data; all 19 acceptance checks pass; findings recorded in [first slice results](first-slice.md#results) | Built |
| 2026-10-01 | Built and tested on stubs: slice 1b model gateway; real Gmail client with read-only OAuth and scope refusal; D16 verification command; Paperless and Immich lookups; read-only Inbox Zero adapter; example config, config check, CI. Local verification handed to the owner | Built |
| 2026-10-02 | Owner's local fixes applied; Gmail sync hardening implemented on synthetic tests (pacing defaults 1 s, 1,200 units/min, 1,800,000 units/day as maximums; shared persistent budget and lock; recorded stops; page-level resume with reconcile and confirmed absence). Live validation and D16 pending | Built (synthetic) |
| 2026-10-03 | D17: optional file discovery foundation on a separate branch; providers are existing tools (Recoll adapter, sist2 slot); separate `files.db`; grants per root and feature in the decisions store. Built on synthetic files only | Accepted (boundary); providers pending |
| 2026-10-04 | D18: container images (`towpath`, `towpath-recoll`) built and tested in GitHub Actions, published to GHCR from trusted refs only, deployed by digest with pull-only Compose | Accepted (owner requirement); first publish pending owner action |
| 2026-10-04 | D18 revised after release review: canonical image per revision with release tags as digest aliases; verified promotion; corresponding source for every bundled distribution package published before binaries; core image drops CPython modules linking GPL or Sleepycat libraries | Accepted; first publish pending owner action |
| 2026-10-04 | D18 source-retention policy: published corresponding-source artifacts are retained indefinitely, including after matching images are retired | Accepted (owner review) |
