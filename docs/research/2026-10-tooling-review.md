# Tooling review, October 2026

Status: **research, not adopted architecture.** This note records what existing tools could do for Towpath as of 2026-10-01. Decisions taken from it are in [decisions](../decisions.md); the working candidate list is [integrations](../integrations.md). Re-check anything here before depending on it.

## Method

Four questions were researched from public sources: mail-management tools, Gmail access rules, life-stream and media tools, and model servers plus a light Python foundation. Where possible, claims were checked against primary sources: license files and source code in each repository, package metadata on PyPI, Google's machine-readable API discovery documents, and published OpenAPI specifications. Several documentation sites, including Google's developer pages, could not be fetched from the research environment, so some Google policy details come from search excerpts and from other projects' documentation. Those are marked *secondary*. "Last activity" means the latest commit or release seen on 2026-10-01.

License classes follow the [license policy](../integrations.md): open source; open source with personal-use terms; not open source.

## Mail management

No existing application fits Towpath's model of reading broadly, acting only on approved proposals, and never sending.

| Project | License | Finding | Use |
| --- | --- | --- | --- |
| [Inbox Zero](https://github.com/elie222/inbox-zero) | AGPL-3.0 plus added commercial and organization-size terms (personal-use terms) | Covers about six of Towpath's seven mail features: categories with corrections, reply tracking, bulk unsubscribe, drafts, digest, filing to cloud drives. Requires `gmail.modify` and `gmail.settings.basic`, with no read-only mode. Its rules run automatically and can send, reply, forward, delete, and archive; the approve-before-run statuses are deprecated. Needs Postgres and Redis. Supports arbitrary OpenAI-compatible endpoints. Very active | Design reference only. Running it would bypass Towpath's approval model |
| [gmailctl](https://github.com/mbrt/gmailctl) | MIT | `gmailctl export` turns a configuration into Gmail's filter XML without any API call or credential | Reference for the filter file format; optionally bundle the binary for export only |
| [Google API Python client](https://github.com/googleapis/google-api-python-client) | Apache-2.0 | Official Gmail, People, and Calendar client | Library |
| Python `email` and `mailbox` | PSF | MIME parsing, Maildir and mbox | Library |
| [talon](https://github.com/mailgun/talon) | Apache-2.0 | Strips quoted replies and signatures, useful before reply detection and drafting | Optional library |
| [mail-parser](https://github.com/SpamScope/mail-parser) | Apache-2.0 | MIME parsing with defect reporting | Optional library |
| [Got Your Back](https://github.com/GAM-team/got-your-back) | Apache-2.0 | Gmail backup with a read-only option | Reference; its full-copy model is not Towpath's |
| [lieer](https://github.com/gauteh/lieer) with notmuch | GPL-3.0+ | Two-way Gmail sync; requires the full mail scope | Not recommended |
| [Zero](https://github.com/Mail-0/Zero) | MIT | AI mail client built on Cloudflare Workers; quiet for about a year | Ideas only |
| [MailScrub](https://github.com/brooksc/MailScrub), [gmail-cleaner](https://github.com/Gururagavendra/gmail-cleaner) | MIT | Unsubscribe and sender-volume tools; broad scopes | Ideas for unsubscribe review |
| [Agents from scratch](https://github.com/langchain-ai/agents-from-scratch) | MIT | Human-in-the-loop triage and approval queue patterns | Ideas for review screens |
| [OpenArchiver](https://github.com/LogicLabs-OU/OpenArchiver) | AGPL-3.0 | Heavy mail archiving service | Not a fit |

Gaps that Towpath writes itself, because no maintained library exists:

- **Unsubscribe parsing:** `List-Unsubscribe` ([RFC 2369](https://www.rfc-editor.org/rfc/rfc2369)) and one-click POST ([RFC 8058](https://www.rfc-editor.org/rfc/rfc8058)); a few dozen lines on the standard library.
- **Bulk mail detection:** `List-Id`, `List-Unsubscribe`, `Precedence`, `Auto-Submitted` ([RFC 3834](https://www.rfc-editor.org/rfc/rfc3834)), and Gmail's category labels.
- **Awaiting reply:** last message not from the person, person addressed directly, thread not bulk, sender has history of replies; then an optional model check.
- **Filter XML:** an Atom feed with Gmail-specific properties (`from`, `to`, `subject`, `hasTheWord`, `label`, `shouldArchive`, `shouldMarkAsRead`, and others), documented best by gmailctl's source. Importing a file through Gmail's settings needs no API permission.

## Gmail access

Primary source: Gmail's [API discovery document](https://gmail.googleapis.com/$discovery/rest?version=v1). Policy details are *secondary*.

| Scope | Allows | Notes |
| --- | --- | --- |
| `gmail.readonly` | Read messages, history, attachments, labels, filters | Needed for part structure (`format=full`). Restricted |
| `gmail.metadata` | Labels and headers only | Disallows search and `format=full`, so no part structure. Unusable for Towpath |
| `gmail.labels` | Manage label definitions only | Cannot change labels on messages |
| `gmail.modify` | Read, change labels, archive, trash, **and send** | No permanent delete. Restricted |
| `gmail.compose` | Drafts **and send** | Restricted |
| `gmail.settings.basic` | Settings and filters | Only scope that creates filters |
| `https://mail.google.com/` | Everything, including permanent delete | Only scope for IMAP over OAuth |

Consequences:

- No scope allows labeling or archiving without also allowing sending. The action runner's "never send" rule must be enforced in its own code: an allowlist of only message-modify and label calls, with tests.
- Public apps using restricted scopes need Google verification and a yearly security assessment (*secondary*). Exceptions include personal use by the developer or a few people they know, under 100 users (*secondary*). A shared Towpath-run OAuth client would not qualify; each self-hoster registering their own client follows the documented pattern of gmailctl, lieer, and Inbox Zero's self-hosting guide.
- An external app left in **Testing** status gets refresh tokens that expire after 7 days, Gmail scopes included. Publishing it **In production** without verification removes the expiry; users click through an unverified-app warning, and a lifetime cap of 100 users applies (*secondary*).
- `messages.get` with `format=full` returns the MIME tree with attachment IDs and sizes but no attachment bytes, which supports indexing broadly and fetching narrowly. `history.list` cursors are typically valid for at least a week; an expired one returns 404 and needs a full sync.
- Quotas for Cloud projects created since May 2026 appear lower, and `messages.get` costs more units. The initial index of a 100,000-message mailbox may take about five to six hours on a new project (*secondary*). Indexing should be incremental, resumable, and show progress, newest mail first.
- **IMAP with an app password** needs no Cloud project, requires 2-Step Verification, and can read part structure (`BODYSTRUCTURE`) and Gmail's IDs and labels (`X-GM-MSGID`, `X-GM-LABELS`). The password is full access, including sending through SMTP and deleting, so read-only behavior would be Towpath's code only. IMAP over OAuth needs the broadest scope and is worse than the Gmail API for reading.

## Model endpoints

| Server | License | Structured output | Embeddings | Notes |
| --- | --- | --- | --- | --- |
| [llama.cpp server](https://github.com/ggml-org/llama.cpp) | MIT | `json_object`, `json_schema`, grammars | Yes, with flags | API key option; official container images |
| [Ollama](https://github.com/ollama/ollama) | MIT | `json_object`, `json_schema` | Yes | No `tool_choice` |
| [vLLM](https://github.com/vllm-project/vllm) | Apache-2.0 | `json_object`, `json_schema`, other structured modes | Pooling models | Its API key does not cover every route; put it behind a proxy |
| [LocalAI](https://github.com/mudler/LocalAI) | MIT | Depends on backend | Yes | |
| LM Studio | App is not open source | `json_schema` | Yes | Can be connected by a person who chooses it; not bundled |

- **Gateways.** [LiteLLM](https://github.com/BerriAI/litellm) is MIT except an `enterprise/` directory; its per-key controls require Postgres, and two of its PyPI releases were compromised in March 2026. Its controls work on keys and models, not on Towpath's data classes or item-level model use. Recommendation: no bundled gateway; Towpath's own thin gateway decides policy before data leaves. Anyone may register a gateway as an ordinary endpoint.
- **Client.** The official [openai Python library](https://github.com/openai/openai-python) (Apache-2.0) reads a default key and base URL from the environment if not given them; Towpath must always pass both explicitly so no implicit endpoint exists.
- **Structured output.** A fallback ladder (`json_schema`, then `json_object`, then prompt-only, then repair and one re-ask) is about 150 lines on top of [Pydantic](https://github.com/pydantic/pydantic) (MIT), [jsonschema](https://github.com/python-jsonschema/jsonschema) (MIT), and [json-repair](https://github.com/mangiucugna/json_repair) (MIT). [instructor](https://github.com/567-labs/instructor) (MIT) implements similar modes but does not fall back across them and hides the raw request the ledger needs; use it as a reference.

## Light Python foundation

| Need | Candidate | License | Note |
| --- | --- | --- | --- |
| CLI | [Typer](https://github.com/fastapi/typer) on Click | MIT, BSD-3 | |
| Storage | Standard-library `sqlite3`, numbered SQL migration files tracked with `PRAGMA user_version` | PSF | Smallest option; [sqlite-utils](https://github.com/simonw/sqlite-utils) (Apache-2.0) as a helper if useful |
| Full-text search | SQLite FTS5 | Public domain | Built in |
| Vector search | [sqlite-vec](https://github.com/asg017/sqlite-vec) | MIT or Apache-2.0 | Pre-1.0; optional |
| Background jobs | [huey](https://github.com/coleifer/huey) with SQLite storage, or a simple jobs table | MIT | procrastinate needs Postgres |
| Later web UI | Django ([D13](../decisions.md#d13-web-framework)); Starlette or FastAPI with Jinja2 and htmx as lighter alternatives | BSD, MIT, 0BSD | Keep domain logic in plain modules over Towpath's own SQL schema |
| Secrets | Compose secrets mounted as files; credential references such as `file:` and `env:` | — | Never store secrets in SQLite or the ledger |

SQLite caveats: all processes must share one host and a local filesystem. A reader whose volume is mounted read-only may fail to open a WAL database unless the WAL side files already exist; `immutable` mode is safe only on snapshots. Single writer, busy timeout, short transactions.

## Documents and photos

**[Paperless-ngx](https://github.com/paperless-ngx/paperless-ngx)** (GPL-3.0, active):

- **API tokens** carry all of their user's permissions. Least privilege therefore means a dedicated view-only user for reading and a separate user that can add documents for delivery.
- **Duplicate check:** documents carry a checksum, SHA-256 in version 3 and MD5 in version 2. A filter looks documents up by checksum, which is the duplicate check before upload.
- **Upload:** a multipart POST with metadata (title, created date, correspondent, tags). It returns a task ID that resolves to the new document ID.
- **Metadata:** the `created` field is a date only. OCR text is available but has no stable span IDs, so citations into documents use document ID, checksum, and page or text offset.
- **Consume folder:** files placed there are moved into Paperless storage. That is acceptable for delivering a copy, but not for reading.

**[Immich](https://github.com/immich-app/immich)** (AGPL-3.0, active):

- **API keys** take explicit permissions, so a read-only key is possible (asset read and view, people and faces, optionally download), with a separate key for upload.
- **Asset data:** assets carry a SHA-1 checksum (base64), the original capture time, the time zone, GPS, and the people recognized in them.
- **Duplicate check:** a bulk upload check endpoint answers "already present?" by checksum.
- **Incremental sync:** a sync stream supports reading only what changed.
- **Face names** are unreviewed labels, not facts. They should enter Towpath as proposals.

**[PhotoPrism](https://github.com/photoprism/photoprism)** (AGPL-3.0, active):

- It has a SHA-1 file lookup and app tokens with scopes.
- Its design records the source of each date and place with a priority order in which manual edits outrank estimates. That is a useful model for Towpath's provenance field.

Design consequence: destinations use different hash algorithms (SHA-256, MD5, base64 SHA-1). Presence matching must compute the hash each destination reports, not only SHA-256.

## Contacts and calendars

- Google People API with `contacts.readonly`, plus `contacts.other.readonly` for automatically saved "other contacts", which helps match email senders. Calendar has read-only scopes. Both support incremental sync tokens.
- [caldav](https://github.com/python-caldav/caldav) (Apache-2.0 or GPL-3.0), [icalendar](https://github.com/collective/icalendar) (BSD-2), [vobject](https://github.com/py-vobject/vobject) (Apache-2.0), and [vdirsyncer](https://github.com/pimutils/vdirsyncer) (BSD-style) for CardDAV through local files.
- Radicale and Baïkal are servers to connect to, not bundle.

## Life stream

No surveyed project combines evidence-span citations, uncertain dates, contradiction tracking, and durable human review. Useful prior art:

| Project | License | Useful for |
| --- | --- | --- |
| [Timelinize](https://github.com/timelinize/timelinize) | AGPL-3.0 | About 30 importers and a timeline schema with time spans, time uncertainty, coordinate uncertainty, and a "modified" flag. Its README warns the schema is unstable. Ideas, and later an optional read-only importer |
| [HPI](https://github.com/karlicoss/HPI) | MIT | Python modules exposing personal data exports as typed iterators; usable as a library |
| [google_takeout_parser](https://github.com/seanbreckenridge/google_takeout_parser) | MIT | Google Takeout importer |
| [promnesia](https://github.com/karlicoss/promnesia) | MIT | Evidence locator pattern (source, position, context) |
| [Perkeep](https://github.com/perkeep/perkeep) | Apache-2.0 | Content-addressed blobs and signed claim history: a model for auditable corrections |
| [Dawarich](https://github.com/Freika/dawarich) | AGPL-3.0 | Location history with suggested, confirmed, and declined visits; an API; integrates with photo libraries. Best location connector |
| [Monica](https://github.com/monicahq/monica) | AGPL-3.0 | Personal relationships; the new major version has a minimal API. Optional |
| [splink](https://github.com/moj-analytical-services/splink) | MIT | Explainable probabilistic matching of people across contacts and mail senders |
| [dedupe](https://github.com/dedupeio/dedupe) | MIT | Matching with active learning from human labels; slower development |

**Dates.** The Library of Congress [Extended Date/Time Format](https://www.loc.gov/standards/datetime/) (now part of ISO 8601-2) expresses uncertain (`?`), approximate (`~`), unspecified (`X`), interval, and season values. [python-edtf](https://github.com/ixc/python-edtf) (MIT, active) parses it and gives strict and fuzzy bounds. A good fit: store the EDTF string as the canonical value, store bounds as indexed columns for sorting and overlap queries, and keep a separate provenance field (where the date came from, with manual edits outranking estimates).

## Recommendations carried into the design

1. Gmail API with `gmail.readonly` in `towpath-connect`; each self-hoster brings their own Google Cloud project and publishes it to production unverified; a step-by-step setup guide. IMAP with an app password as an optional quick start is an owner decision ([D14](../decisions.md#d14-gmail-access-for-other-self-hosters)).
2. The action runner enforces "never send" with a call allowlist, because `gmail.modify` can send.
3. Initial indexing is incremental, resumable, newest first, with progress shown.
4. Filter files are generated by Towpath in Gmail's XML format, using gmailctl as the format reference; people import them through Gmail's settings.
5. Inbox Zero is a design reference, not a bundled service.
6. A light first stack: Typer, `sqlite3` with SQL migrations, FTS5, the openai library with explicit endpoints, Pydantic, jsonschema, json-repair; no bundled gateway, no web framework yet.
7. Paperless-ngx and Immich through their APIs with separate read and write identities; presence matching uses each destination's hash algorithm.
8. People matching with deterministic keys first and splink for fuzzy matches, stored as proposals, never merges.
9. Dates as EDTF with materialized bounds and a provenance field.
