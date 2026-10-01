# Services, stores, and permissions

Status: **designed, not built.** Service names are working names.

## Services

Towpath is one Python codebase ([D2](decisions.md#d2-implementation-language-and-storage)) run as four services in Docker Compose. Separate containers let each service hold only the credentials it needs.

| Service | Does | Holds credentials for | Required? |
| --- | --- | --- | --- |
| `towpath-web` | Login, UI, review and approval screens, configuration; links to each service's connection setup | None for external accounts | Yes |
| `towpath-worker` | Analysis jobs, scans, classification, draft generation, claim proposals, question answering; the only service that calls models | Model endpoints only | Yes |
| `towpath-connect` | Read adapters for mail, contacts, document systems, photo libraries, calendars; fetches content on request; runs read-only connection setup | Read access to connected accounts and tools | Yes, once anything is connected |
| `towpath-act` | Executes approved, frozen proposals: mailbox changes, drafts if allowed, deliveries to document or photo systems; runs write-access connection setup | Write access, per allowlisted action | No; added in [roadmap](roadmap.md) milestone 5 |

**Why web and worker hold no account credentials.** Worker parses untrusted mail and sends it to models; web renders it. If injected content or a bug subverts either, it should find nothing that can read more of an account or change one.

**Why connection setup runs in the credential-holding service.** When a person clicks "Connect Gmail" in the UI, the request goes to `towpath-connect`'s setup endpoint, which runs the provider's OAuth flow and stores the resulting token in its own secret volume. Granting write access is a separate consent flow run by `towpath-act`, producing a separate token. The web UI shows status and scopes but never sees a token.

## Docker Compose layout

| Compose profile | Services | Purpose |
| --- | --- | --- |
| default | `towpath-web`, `towpath-worker`, `towpath-connect` | Mail management review and life stream, read-only |
| `actions` | `towpath-act` | Approved mailbox changes and deliveries |
| `models` | A bundled OpenAI-compatible model server ([candidates](integrations.md#models)) | For people without an existing endpoint |
| `documents` | A bundled document system ([candidates](integrations.md#documents)) | For people without an existing one |
| `photos` | A bundled photo library ([candidates](integrations.md#photos)) | For people without an existing one |

Every bundled tool is optional and interchangeable with an existing deployment: the person enters its URL and credential in Towpath instead of enabling the profile. Towpath's own services never require a bundled tool.

Volumes: one data volume per store (below), mounted read-write only in the writing service and read-only elsewhere, and one secret volume per credential-holding service. The Compose file and an `.env.example` in the repository will contain placeholders only ([publication rules](publication.md)).

## Stores and ownership

Human decisions are kept apart from everything rebuildable, so backups can focus on what cannot be regenerated. Every store has one writing service.

| Store | Writer | Readers | Contents | Rebuildable? |
| --- | --- | --- | --- | --- |
| Source index | `towpath-connect` | worker, web, act (IDs only) | Connections, sync runs, cursors, item metadata and part structure, dated observations (labels, folders), content cache, external references (document and photo IDs) | Mostly, by resyncing |
| Work queue | web and worker (append only) | `towpath-connect` | Content requests: which item or part to fetch, for which feature | Yes |
| Derived store | worker | web | Classifications, sender and list profiles, triage results, proposals, scans and matches, people and event candidates, proposed claims, embeddings | Yes |
| Decisions store | web | worker, act | Approvals with frozen proposal copies, rejections, corrections, accepted claims, recollections, visibility settings, source grants, model endpoint grants, preservation choices | **No.** Back this up |
| Preserved artifacts | worker, on a recorded preservation choice | web | Exact bytes a person chose to keep, content-addressed, with provenance ([preserved artifacts](scans-and-destinations.md#preserved-artifacts)) | **No.** Back this up |
| Model ledger | worker | web | Endpoint profiles in use, capability reports, call records | No; audit record |
| Action ledger | `towpath-act` | web | Execution attempts and per-item receipts | No; audit record |
| Secrets | Each credential-holding service, its own volume | That service only | OAuth tokens, API keys | Not in any store above |

SQLite files, one per store, are the first implementation. Two points need verification before relying on them: SQLite's write-ahead log across containers with read-only mounts, and concurrent appends to the work queue. If either fails, readers go through a small read API on the owning service instead.

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
| Mail management, review only | default profile | Approved items export as checklists or generated filter files |
| Mail management with actions | adds `actions` | Tier 1 mailbox changes and deliveries per [D1](decisions.md#d1-mailbox-and-destination-execution) |
| Life stream from mail | default profile | Needs a life grant on the mail connection |
| Life stream without mail | default profile | Contacts, photo library, document system, calendars, recollections |
| Existing tools | default profile plus connections | Point at existing document or photo systems and model endpoints |
| Everything bundled | all profiles | For a fresh self-hosted setup |

## One message, end to end

1. `towpath-connect` indexes a message: native IDs, labels observed at this sync, dates, and MIME part structure. No body or attachment is downloaded yet.
2. Worker's triage rules need the body, so worker enqueues a content request; `towpath-connect` fetches that one message and caches it.
3. Worker classifies it and, if a model task is bound and the data class is granted, calls the endpoint. It proposes a label and notes a PDF attachment that a scan matches for the document system.
4. The person approves the label in the web UI and approves sending the PDF to their document system. Web records both approvals with frozen copies.
5. `towpath-act` rechecks the message's labels, applies the label, uploads the PDF, and writes receipts. The next sync observes the new label; the document system now holds the PDF, and Towpath stores its document ID as a reference.
