# Integrations

Status: **candidates only.** Nothing in this document is adopted. Each entry needs its current behavior, API, license, and maintenance checked before Towpath depends on it, and that check should be recorded in [decisions](decisions.md). Projects change quickly; earlier impressions of them are not evidence.

**License policy.** Towpath itself is MIT. A component it uses must have its source code published and be free to use for personal self-hosting with Towpath's goals:

| Class | Meaning | How Towpath may use it |
| --- | --- | --- |
| Open source | OSI-approved license with no added restrictions (MIT, Apache-2.0, BSD, GPL, AGPL, and similar) | Required dependency, bundled service, or optional integration |
| Open source, personal-use terms | Source published and free for personal use, but with added limits on commercial use, organization size, or field of use | Optional integration or optional Compose profile only, labeled with its terms; never required by Towpath's core features; its code is never copied into Towpath |
| Not open source | No published source, or no free personal use | Not bundled. A person may still connect a service they choose, such as their mail provider or a hosted model API |

The middle class keeps Towpath's own code usable by anyone while still letting a personal deployment benefit from tools that are free for that use.

Towpath prefers, in order: an existing deployment the person already runs, a bundled open-source service, an existing library, and only then new code ([integrate first](architecture.md#integrate-first)).

## Evaluation checklist

For every candidate, record:

| Question | Why it matters |
| --- | --- |
| License class (above), and whether Towpath calls it as a separate service or includes its code | Decides whether it can be required, bundled, or only optional. Included code must be MIT-compatible; separate services can carry other licenses |
| Maintained recently, with a documented, versioned API | Towpath's adapter breaks if the API drifts |
| Can Towpath point at an existing instance, and can Compose bundle one? | Both modes are required for tools people often already run |
| Least-privilege access (read-only tokens, scoped keys) | Read adapters belong in `towpath-connect`, writes in `towpath-act` |
| Leaves originals in place and supports export | Towpath references, not owns, other systems' files |
| Tested subset | Towpath claims support only for the API calls its tests exercise |

## Mail

| Need | Candidate | Kind | Notes |
| --- | --- | --- | --- |
| Gmail read and tier 1 actions | Gmail API with Google's official Python client libraries | Library | Accepted direction ([D3](decisions.md#d3-first-real-mail-source)); thin Towpath adapter in `towpath-connect` and `towpath-act` |
| MIME parsing | Python standard library `email` and `mailbox` | Library | Also reads Maildir and mbox |
| Other providers | IMAP client libraries; JMAP clients | Library | Later; IMAP grants all-or-nothing access |
| Local mail sync and indexing | Gmail-to-Maildir sync tools paired with a local mail indexer | Bundled service | Considered, not proposed: they keep a full local copy of the mailbox, which conflicts with [fetching narrowly](architecture.md#design-rules) and is not Towpath's purpose |
| Rules, unsubscribe review, reply tracking, classification | Inbox Zero | Possible optional service | License class: open source with personal-use terms (AGPL-3.0 plus added commercial-use and organization-size restrictions; free for personal use). Fit under review: models, permissions, automatic actions |
| Generated rules | Gmail's filter import file format; Sieve for other providers | Format | Verify Gmail's current import format |
| Unsubscribe metadata | `List-Unsubscribe` (RFC 2369) and one-click (RFC 8058) headers | Standard | No tool needed |

## Documents

| Need | Candidate | Kind | Notes |
| --- | --- | --- | --- |
| Document system as destination and evidence source | Paperless-ngx | Existing deployment or bundled service | Verify: API token scopes, listing documents with checksums for duplicate detection, upload with metadata, consume-folder behavior |

## Photos

| Need | Candidate | Kind | Notes |
| --- | --- | --- | --- |
| Photo library as destination and evidence source | Immich | Existing deployment or bundled service | Verify: API key permissions, asset listing with dates, locations and checksums, upload, and whether recognized people are exposed |
| Alternative | PhotoPrism | Existing deployment or bundled service | Same checks |

## Sources

| Source | Candidate | Kind | Notes |
| --- | --- | --- | --- |
| Contacts | Google People API (read-only); CardDAV; vCard files | API, standard | Gmail users' contacts first |
| Calendars | iCalendar files; CalDAV; Google Calendar API | Standard, API | |
| Local mail archive | Maildir or mbox, read with the standard library | Standard | An ordinary optional source for anyone who has one; no special features |

## Life stream sources

| Need | Candidate | Kind | Notes |
| --- | --- | --- | --- |
| Importing personal data exports into a timeline | Timelinize | Possible importer or evidence index | Not Towpath's core: it would supply evidence references, while claims and review stay in Towpath. Verify data model, API, and license |
| Packaging a future family or archive edition | BagIt (RFC 8493) directory layout | Standard | Candidate format for exports with checksums |

## Models

| Need | Candidate | Kind | Notes |
| --- | --- | --- | --- |
| Model endpoint | Any OpenAI-compatible endpoint the person configures | Existing deployment | See [model providers](model-providers.md); Poundlock is one optional choice |
| Bundled local model server | llama.cpp server; Ollama's OpenAI-compatible API; vLLM on GPU hosts | Bundled service | Verify structured-output and embeddings support per server with the [capability probe](model-providers.md#capability-probing) |

## Search

| Need | Candidate | Kind | Notes |
| --- | --- | --- | --- |
| Full-text search | SQLite FTS5 | Library | First choice; no extra service |
| Larger-scale or typo-tolerant search | Meilisearch, Typesense | Bundled service | Only if FTS5 proves insufficient |
| Semantic search | Embeddings from a configured endpoint, stored with SQLite | Library | Off unless an embeddings endpoint is bound |

## Web platform

| Need | Candidate | Kind | Notes |
| --- | --- | --- | --- |
| Web framework | None in the first builds; Django is the leading candidate for the later login and review UI | Library | [D13](decisions.md#d13-web-framework) |
| Background jobs | A SQLite-backed task queue, or the framework's own task support | Library | Verify behavior across containers |
| Login | Built-in single-user login first; optional OpenID Connect or forward-auth from an existing identity provider | Library, existing deployment | Multi-person households later |
| Routing setup endpoints | A bundled reverse proxy such as Caddy or Traefik | Bundled service | Lets connection setup reach `towpath-connect` and `towpath-act` through one address |

## What Towpath writes itself

The web UI and review workflow; adapters to each tool above; the proposal, approval, and receipt model; the action runner; the evidence, claim, and correction model of the [life stream](life-stream.md); the model gateway; and the Compose file.
