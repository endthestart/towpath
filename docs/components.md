# Services, stores, and permissions

Status: **design, partly built.** Service names are working names. The connect and worker roles exist as CLI command groups sharing SQLite stores (source index, work queue, derived, decisions, ledger); `towpath-web` and `towpath-act` do not exist yet. Built pieces are listed in the [roadmap](roadmap.md); everything else here is design.

## Services

Towpath is one Python codebase ([D2](decisions.md#d2-implementation-language-and-storage)) run as four services in Docker Compose. Separate containers let each service hold only the credentials it needs.

| Service | Does | Holds credentials for | Required? |
| --- | --- | --- | --- |
| `towpath-web` | Login, UI, review and approval screens, configuration; links to each service's connection setup | None for external accounts | Yes |
| `towpath-worker` | Analysis jobs, scans, classification, draft generation, claim proposals, question answering; the only service that calls models | Model endpoints only | Yes |
| `towpath-connect` | Read adapters for mail, contacts, document systems, photo libraries, calendars; fetches content on request; runs read-only connection setup | Read access to connected accounts and tools | Yes, once anything is connected |
| `towpath-act` | Executes approved, frozen proposals: deliveries to document or photo systems; runs write-access connection setup for them | Write access to destinations, per allowlisted action | No; added with attachment routing ([roadmap](roadmap.md)) |

**Why web and worker hold no account credentials.** Worker parses untrusted mail and sends it to models; web renders it. If injected content or a bug subverts either, it should find nothing that can read more of an account or change one.

**Why connection setup runs in the credential-holding service.** When a person clicks "Connect Gmail" in the UI, the request goes to `towpath-connect`'s setup endpoint, which runs the provider's OAuth flow and stores the resulting token in its own secret volume. Granting write access is a separate consent flow run by `towpath-act`, producing a separate token. The web UI shows status and scopes but never sees a token.

## Docker Compose layout

| Compose profile | Services | Purpose |
| --- | --- | --- |
| default | `towpath-web`, `towpath-worker`, `towpath-connect` | Mail management review and life stream, read-only |
| `mail` | The mail-management provider, Inbox Zero first, with its database and cache ([mail management](mail-management.md)) | Mail management with its own Gmail write access; can instead point at an existing instance |
| `actions` | `towpath-act` | Approved deliveries to destinations |
| `models` | A bundled OpenAI-compatible model server ([candidates](integrations.md#models)) | For people without an existing endpoint |
| `documents` | A bundled document system ([candidates](integrations.md#documents)) | For people without an existing one |
| `photos` | A bundled photo library ([candidates](integrations.md#photos)) | For people without an existing one |

Every bundled tool is optional and interchangeable with an existing deployment: the person enters its URL and credential in Towpath instead of enabling the profile. Towpath's own services never require a bundled tool.

Volumes: one data volume per store (below), mounted read-write only in the writing service and read-only elsewhere, and one secret volume per credential-holding service. The Compose file and an `.env.example` in the repository will contain placeholders only ([publication rules](publication.md)).

## Stores and ownership

Human decisions are kept apart from everything rebuildable, so backups can focus on what cannot be regenerated. Every store has one writing service.

| Store | Writer | Readers | Contents | Rebuildable? |
| --- | --- | --- | --- | --- |
| Source index | `towpath-connect` | worker, web, act (IDs only) | Connections, sync runs, cursors, item metadata and part structure, dated observations (labels, folders), content cache, destination entries and dated lookup results, external references (document and photo IDs) | Mostly, by resyncing |
| Work queue | web and worker (append only) | `towpath-connect` | Content requests (which item or part to fetch) and presence requests (which checksum to look up at a destination) | Yes |
| Derived store | worker | web | Classifications, proposals, scans and matches, people and event candidates, proposed claims, embeddings | Yes |
| Decisions store | web | worker, act | Approvals with frozen proposal copies, rejections, corrections, accepted claims, recollections, audience and model-use settings, source grants, model endpoint grants, preservation choices | **No.** Back this up |
| Preserved artifacts | worker, on a recorded preservation choice | web | Exact bytes a person chose to keep, content-addressed, with provenance ([preserved artifacts](scans-and-destinations.md#preserved-artifacts)) | **No.** Back this up |
| Model ledger | worker | web | Endpoint profiles in use, capability reports, call records | No; audit record |
| Action ledger | `towpath-act` | web | Execution attempts and per-item receipts | No; audit record |
| Secrets | Each credential-holding service, its own volume | That service only | OAuth tokens, API keys | Not in any store above |

SQLite files, one per store, are the first implementation. All services must share one host and a local filesystem. A reader whose volume is mounted read-only may fail to open a database in write-ahead-log mode unless its side files already exist ([tooling review](research/2026-10-tooling-review.md#light-python-foundation)); options are to mount read-write and open read-only in code (weaker), or to give readers a small read API on the owning service (stronger). Decide when building the Compose file; the CLI slices run as separate processes on one host and open read-only in code.

## Permission matrix

R = read, W = write, — = none.

| Service | Read credentials | Write credentials | Model credentials | Source index | Work queue | Derived | Decisions | Preserved | Model ledger | Action ledger |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `towpath-web` | — | — | — | R | W | R | W | R | R | R |
| `towpath-worker` | — | — | R | R | W | W | R | W | W | R |
| `towpath-connect` | R | — | — | W | R | — | R (grants only) | — | — | — |
| `towpath-act` | — | R | — | R (IDs only) | — | — | R (approvals) | R (to deliver) | — | W |

Source grants decide which features may use which source: a mailbox connected for mail management is not life-stream evidence until a person grants it. Grants are enforced in worker's and web's read layer. That is a code-level boundary inside one service, weaker than the credential boundaries above, and documented as such.

## Usage modes

| Mode | Services | Notes |
| --- | --- | --- |
| Mail management | `mail` profile (or an existing Inbox Zero instance) | Towpath shows an overview and links into it |
| Attachment routing | default profile plus `actions` | Approved deliveries to a document system or photo library |
| Life stream from mail | default profile | Needs a life grant on the mail connection |
| Life stream without mail | default profile | Contacts, photo library, document system, calendars, recollections |
| Existing tools | default profile plus connections | Point at existing document or photo systems and model endpoints |
| Everything bundled | all profiles | For a fresh self-hosted setup |

## One message, end to end

1. `towpath-connect` indexes a message: native IDs, labels observed at this sync, dates, and MIME part structure, requested with a field mask that excludes body data ([D16](decisions.md#d16-gmail-structure-without-content)). No body or attachment is stored yet.
2. A scan needs the attachment's bytes to hash it, so worker enqueues a content request; `towpath-connect` fetches that one part and caches it.
3. A scan for PDF statements matches its attachment, and the document system does not hold that file yet, so worker proposes a delivery.
4. Separately, the mail-management tool may label or archive the same message under its own rules; the next sync observes that as a dated change.
5. The person approves sending the PDF to their document system. Web records the approval with a frozen copy; `towpath-act` checks the document system for the file's hash, uploads it, and writes a receipt. Towpath stores the new document ID as a reference.
