# Integrations

Status: **candidates, researched 2026-10-01.** Adapters now exist for Gmail (Google's API client), Paperless-ngx and Immich (read-only lookups), Inbox Zero (read-only), and OpenAI-compatible endpoints; all are tested against stubs or fakes only, and none is adopted until checked against a running instance ([local quickstart](setup/local-quickstart.md)). Findings and sources are in the [October 2026 tooling review](research/2026-10-tooling-review.md). Each entry still needs a hands-on check of the exact API calls Towpath will use before it becomes a dependency; record that in [decisions](decisions.md).

**License policy.** Towpath itself is MIT. A component it uses must have its source code published and be free to use for personal self-hosting with Towpath's goals:

| Class | Meaning | How Towpath may use it |
| --- | --- | --- |
| Open source | OSI-approved license with no added restrictions (MIT, Apache-2.0, BSD, GPL, AGPL, and similar) | Required dependency, bundled service, or optional integration |
| Source available with use restrictions | Source published and free for personal self-hosting, but with added limits (for example on commercial use or organization size) that conflict with the [Open Source Definition](https://opensource.org/osd) | Optional integration or optional Compose profile only, labeled with its terms; never required by Towpath's core features; its code is never copied into Towpath |
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

| Need | Candidate | License class | Role | Finding |
| --- | --- | --- | --- | --- |
| Mail management (categories, reply tracking, unsubscribe, drafts, rules, digest) | Inbox Zero | Source available with use restrictions | Optional Compose service `mail`: Towpath's first mail-management provider ([D15](decisions.md#d15-role-of-inbox-zero)) | Holds its own Gmail write access; rules act automatically; accepts an OpenAI-compatible endpoint; API and webhook action to verify ([mail management](mail-management.md#integration-points)) |
| Gmail read access for Towpath | Google API Python client | Open source (Apache-2.0) | Library in `towpath-connect` | `gmail.readonly` with a field mask that must exclude body data at every level ([D16](decisions.md#d16-gmail-structure-without-content)) |
| MIME parsing | Python `email` and `mailbox`; optionally mail-parser | Open source | Library | Standard library is enough for the first slice |
| Clean text for life-stream extraction | talon | Open source (Apache-2.0) | Optional library | Strips quoted replies and signatures |
| Alternatives or Towpath-built replacements, later | gmailctl (filter export), MailScrub and gmail-cleaner (unsubscribe ideas) | Open source (MIT) | Reference | Only if the provider stops fitting |
| Other providers, later | IMAPClient or imap_tools; JMAP clients | Open source | Library | IMAP credentials are all-or-nothing |
| Full local Gmail sync | lieer with notmuch, Got Your Back | Open source | Not used | Full-copy model and broad scopes do not fit fetching narrowly |

## Documents

| Need | Candidate | License class | Role | Finding |
| --- | --- | --- | --- | --- |
| Document system | Paperless-ngx | Open source (GPL-3.0) | Existing deployment or bundled service, over its REST API | Tokens carry all of a user's permissions, so use a view-only user for `towpath-connect` and a separate add-only user for `towpath-act`. Checksum lookup (SHA-256 in version 3, MD5 in version 2) for duplicate checks; upload with metadata; `created` is date-only. Deliver through the API or consume folder; never read through the consume folder |

## Photos

| Need | Candidate | License class | Role | Finding |
| --- | --- | --- | --- | --- |
| Photo library | Immich | Open source (AGPL-3.0) | Existing deployment or bundled service, over its API | Scoped API keys allow a read-only key and a separate upload key. SHA-1 checksums with a bulk "already present?" check; capture time, time zone, GPS, recognized people; incremental sync |
| Photo library, alternative | PhotoPrism | Open source (AGPL-3.0) | Connector | SHA-1 lookup; date and place source priority worth copying as a design |

Destinations report different hash algorithms, so presence checks compute the algorithm each destination uses ([scans and destinations](scans-and-destinations.md#destination-connectors)).

## Sources

The broader source backlog includes IMAP, iMessage, file/project manifests, old mailbox backups, Takeout and Facebook exports. Existing source-specific importers are candidates to verify before adoption. The [vision](vision.md) records this scope; listing a source does not claim current support.

| Source | Candidate | License class | Notes |
| --- | --- | --- | --- |
| Contacts | Google People API (`contacts.readonly`, plus `contacts.other.readonly` for automatically saved contacts); CardDAV through vdirsyncer and vobject | Open source libraries | Incremental sync tokens |
| Calendars | Google Calendar read-only scopes; caldav and icalendar | Open source libraries | |
| Location history | Dawarich API | Open source (AGPL-3.0) | Suggested, confirmed, and declined visits match Towpath's review model |
| Personal data exports | HPI; google_takeout_parser | Open source (MIT) | Library importers |
| Local mail archive | Maildir or mbox through the standard library | Open source | An ordinary optional source |

## Life stream

| Need | Candidate | License class | Role |
| --- | --- | --- | --- |
| Claims, citations, review | Towpath code | — | No surveyed project combines evidence spans, uncertain dates, contradictions, and durable review |
| Uncertain dates | python-edtf (EDTF, ISO 8601-2) | Open source (MIT) | Library: EDTF string as canonical value, bounds as indexed columns, plus Towpath's provenance field |
| Matching people | Deterministic keys, then splink | Open source (MIT) | Library; results are proposals, never merges |
| Timeline import and design ideas | Timelinize | Open source (AGPL-3.0) | Ideas now; optional read-only importer once its schema stabilizes |
| Auditable corrections, evidence locators | Perkeep, promnesia | Open source | Design references |
| Relationships | Monica | Open source (AGPL-3.0) | Optional connector |
| Export packages, later | BagIt (RFC 8493) layout | Standard | Candidate format |

## Models

| Need | Candidate | License class | Notes |
| --- | --- | --- | --- |
| Endpoint | Any OpenAI-compatible endpoint the person configures | — | [Model providers](model-providers.md); Poundlock optional |
| Bundled local server | llama.cpp server, Ollama, vLLM, LocalAI | Open source (MIT, MIT, Apache-2.0, MIT) | All support `json_schema`; embeddings and other features vary, so probe each |
| Client | openai Python library | Open source (Apache-2.0) | Always pass base URL and key explicitly; never rely on its environment defaults |
| Validation and fallbacks | Pydantic, jsonschema, json-repair | Open source (MIT) | Towpath's own fallback ladder on top; instructor as a reference |
| Gateway | None bundled | — | Towpath's thin gateway enforces data policy; a person may register LiteLLM or another gateway as an ordinary endpoint |

## Foundation

| Need | Candidate | License class | Notes |
| --- | --- | --- | --- |
| CLI | Typer | Open source (MIT) | |
| Storage and migrations | Standard-library `sqlite3`, numbered SQL files with `PRAGMA user_version` | Open source | sqlite-utils as an optional helper |
| Search | SQLite FTS5; sqlite-vec later for vectors | Open source | sqlite-vec is pre-1.0 |
| Background jobs | huey with SQLite storage, or a jobs table | Open source (MIT) | Not needed in the first slice |
| Later web UI | Django ([D13](decisions.md#d13-web-framework)) | Open source (BSD-3) | Starlette or FastAPI with Jinja2 and htmx as lighter alternatives |
| Login, later | Built-in single-user login; optional OpenID Connect or forward-auth | Open source | |
| Secrets | Compose secrets as files; `file:` and `env:` references | — | Never stored in SQLite or the ledger |

## What Towpath writes itself

The UI and review workflow; adapters to each tool above, including the mail-management provider interface; the proposal, approval, and receipt model; the action runner and its allowlist; the evidence, claim, and correction model of the [life stream](life-stream.md); the model gateway and fallback ladder; and the Compose file.
