# Components, data ownership, and permissions

Status: **designed, not built.** Names such as `towpath-connect` are working names for process roles, not published commands.

## Deployable units

Towpath is one codebase with three process roles. A small deployment can run all of them on one machine; the separation is about which secrets each process can read, not about running many services.

| Unit | Contains | Holds credentials for | Required? |
| --- | --- | --- | --- |
| `towpath` app | CLI and later web UI, review workflows, mail module, life module, scan engine, preservation, background analysis jobs, inference gateway, exporter | Model endpoints only (per bound task) | Yes |
| `towpath-connect` | Source connectors: mail provider, mail archive, calendar files, document and media metadata, destination indexes; fulfills content requests | Source and destination read credentials only | Yes, if any external source is used |
| `towpath-act` | Action runner: mailbox actions and deliveries to [destinations](scans-and-destinations.md) | One write credential per allowlisted mailbox or destination | No. Not in the first releases; scope is an [open decision](decisions.md#d1-mailbox-and-destination-execution) |

External to Towpath: mail providers, destinations such as a document system or media archive, the mail archive and the tool that maintains it, model endpoints (Poundlock or any other), and the personal Gmail evacuation project.

**Why the connector runner is separate from the app.** The app parses untrusted content and sends it to models. If a malformed message, prompt injection, or bug compromises app logic, it should find no credential that can read more mail or change a mailbox. The connector runner does no analysis and calls no model.

**Why the action runner is separate from both.** It is the only code that can change a mailbox or write to another system. It reads approved proposals and its own receipts, nothing else, and can be absent from a deployment entirely.

### Enforcement levels

The design states plainly how strong each boundary is in a given deployment:

| Level | How units are separated | What it protects against |
| --- | --- | --- |
| 1. Module | One process; credentials for disabled roles not loaded | Accidental coupling in code |
| 2. Process and secret | Separate processes; each reads only its own secret file or environment | App-level bugs and injected content reaching credentials |
| 3. OS user or container | Separate users or containers; store files permissioned per owner | A compromised app process reading other units' secrets or writing their stores |

The first slice runs at level 1 with no credentials at all. Any release that holds a real provider credential should support level 2. Mailbox write support should require level 3. Delivery to a folder destination needs no credential and can run at level 2.

## Stores and ownership

Every store has exactly one writer. Other units read through the interfaces in [interfaces](interfaces.md). The first implementation can use one SQLite file per store so file permissions can enforce level 3; a shared database server with per-role grants is a later option.

| Store | Sole writer | Readers | Contents | Rebuildable? |
| --- | --- | --- | --- | --- |
| Source store | `towpath-connect` | App (through grants), `towpath-act` (account and native IDs only) | Source descriptors, sync runs, cursors, occurrences with metadata and MIME part structure, observed state, content cache, destination indexes, coverage reports, cross-source matches | Partly: re-sync can rebuild it while the source still holds the items |
| Work queue | App | `towpath-connect` | Content requests: which occurrence or part to fetch, for which scan or person, with priority | Yes |
| Mail store | App, mail module | App, `towpath-act` (approved proposals only) | Classifications, sender and list profiles, triage results, mailbox proposals, approvals | Classifications yes; proposals and approvals no |
| Scan store | App, scan engine | App, `towpath-act` (approved deliveries only) | Selectors, scan runs and coverage, matches, delivery proposals, approvals | Matches yes; proposals and approvals no |
| Preserved artifacts | App, preservation | App, exporter | Content-addressed bytes kept by a person's choice, with provenance per occurrence ([preserved artifacts](scans-and-destinations.md#preserved-artifacts)) | No; this is the durable copy |
| Life store | App, life module | App, exporter | Recollections (authored evidence), entities, claims, captured excerpts and links to preserved artifacts, review history, narratives, release decisions | Claims proposed by rules or models yes; reviews, recollections, and captured citations no |
| Model ledger | App, inference gateway | App | Endpoint profiles in use, capability reports, data-class grants, call records (endpoint fingerprint, model, prompt version, input fingerprint, outcome) | No; it is an audit record |
| Action ledger | `towpath-act` | App | Execution attempts and per-item receipts for mailbox actions and deliveries | No |
| Configuration and secrets | Person deploying | Each unit reads its own section | Endpoint profiles, source definitions, grants, secret references | Not stored in the repository |

The app cannot fetch from a provider. When it needs content that is not cached, it writes a content request; `towpath-connect` fetches that one item or part, records its hash, and caches it. Preservation copies bytes from the cache only after checking them against that hash.

Recollections are written by the life module, not by a connector, because a person types them into Towpath. They are still evidence: the original wording is immutable and edits are new versions.

## Permission matrix

R = read, W = write, — = no access.

| Unit | Read credentials | Write credentials | Model credential | Source store | Work queue | Mail store | Scan store | Life store | Preserved artifacts | Model ledger | Action ledger |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `towpath-connect` | R | — | — | W | R | — | — | — | — | — | — |
| App: mail module | — | — | via gateway | R (mail grant) | W | W | — | — | — | via gateway | R |
| App: scan engine | — | — | via gateway | R (per grant) | W | — | W | — | — | via gateway | R |
| App: life module | — | — | via gateway | R (life grant) | W | — | — | W | R | via gateway | — |
| App: preservation | — | — | — | R (hashes, cache) | W | — | — | — | W | — | — |
| App: inference gateway | — | — | R | — | — | — | — | — | — | W | — |
| App: exporter | — | — | — | — | — | — | — | R (released items) | R (released items) | — | — |
| `towpath-act` | — | R | — | R (IDs only) | — | R (approved) | R (approved) | — | R (items to deliver) | — | W |

Grants between the mail and life modules are checked in the app's read layer, inside one process. That is a level 1 boundary: it prevents accidental use of mail as life evidence, not misuse by a compromised app. The credential boundaries above are the stronger ones.

Two cross-module reads are deliberately absent. The mail module cannot read life claims, and the life module cannot read proposals or approvals. One narrow exception is optional: when both modules are enabled, the life module may publish a list of occurrence IDs cited by accepted claims, so the mail review screen can warn "cited in your life summary" before a person approves archiving or trashing it. That list carries no claim content.

## Usage modes

| Mode | Units | Stores | Credentials | Notes |
| --- | --- | --- | --- | --- |
| Mail management, mail stays at provider | app (mail), connect | source, mail | provider read; model optional | Proposals are reviewed and can be exported for manual execution |
| Mail management with execution | adds `towpath-act` | adds action ledger | adds provider write | Only after the [mailbox action decision](decisions.md#d1-mailbox-and-destination-execution) |
| Mail management over an archive | app (mail), connect (archive) | source, mail | none beyond file read access | Analysis and search only; there is no account to act on |
| Life summary only | app (life), connect (calendar, files) or none | source, life | source-specific or none | Recollections alone are a valid starting point |
| Both | all of the above | all | union | Mail reaches life summary only through an explicit grant with a scope (accounts, labels, dates) |
| Find and route | app (scan engine), connect, optionally `towpath-act` | source, scan, preserved artifacts | source read; destination read; destination write only if delivering | Without the action runner, approved deliveries can still be exported as files for manual import |
| Gmail evacuation | not Towpath | its own archive | its own | Towpath can read the resulting archive like any other source |

## Data flow for one message

1. `towpath-connect` indexes a message from a provider: native IDs, observed labels, header and provider dates, and MIME part structure. Bodies and attachments are fetched when a rule, scan, or person requests them, then cached and evictable.
2. The mail module classifies it with deterministic rules; if a task is bound to a model endpoint and the data class is granted, the gateway sends a minimal prompt and validates the result.
3. The mail module may create a proposal (for example, add label "Newsletters") that freezes the target occurrence, its native ID, and the state observed at run N.
4. If the source has a life grant covering this message, the life module may propose a claim that cites a span of it. Accepting the claim captures the excerpt and hash, and the person may also preserve the full message or an attachment.
5. If a person approves the mail proposal and an executor exists, the executor rechecks provider state, acts, and writes a receipt. The next sync observes the new state. The life claim keeps its captured citation either way.
