# Life stream

Status: **designed, not built.** Comes after mail management in the [roadmap](roadmap.md), but does not depend on mail.

The life stream helps a person uncover forgotten memories, connect timelines across sources, ask questions with cited answers, and, in the long term, curate a life story to share with family. That family story is a long-term outcome; early milestones only need to produce reviewable, cited claims.

## The evidence rule

AI proposes facts; evidence establishes them.

- Every claim cites evidence: a span of a message, a photo, a document, a calendar entry, or a recollection.
- A claim records what the evidence supports. A booking confirmation supports "a trip was planned", not "the trip happened"; a photo taken at a place supports "was there on that date".
- Dates carry their precision: an exact time, a day, a month, a season, a range, or "before 2010".
- Contradictory evidence is kept side by side and shown, not resolved silently.
- A person's correction or acceptance is durable and survives reprocessing and model changes.
- Recollections keep their original wording and author, and are cited as attributed memory, not as verified fact.

No reviewed graph or timeline project combined evidence-level citations, uncertain dates, conflicting claims, and durable human review, so this layer is the core of Towpath's own code. Importers and indexes around it should be existing tools where possible ([integrations](integrations.md#life-stream-sources)).

## Sources

| Source | Used for | Access | Towpath keeps |
| --- | --- | --- | --- |
| Mail (with a life grant) | People, events, plans, receipts, travel, correspondence over time | The mail connection, through a separate grant scoped by account, labels, or dates | References, cited excerpts, optional preserved items |
| Contacts | People and their identifiers (addresses, numbers) | Contacts API or CardDAV, read-only | References and identifiers |
| Photo library | When and where, who appears (if the library exposes it), events | The library's API, read-only | Asset IDs and metadata; never the originals |
| Document system | Statements, certificates, letters, records with dates | The system's API, read-only | Document IDs and metadata; never the originals |
| Calendars | Scheduled events and their dates | iCalendar files, CalDAV, or a provider API | References |
| Recollections | First-person memories | Typed or dictated in Towpath | The original wording, author, and described period |

Any of these can be the only source. Mail is first in the roadmap because it is connected first, not because the life stream needs it.

## Records

| Record | Meaning |
| --- | --- |
| Evidence reference | A pointer to an item in a source (message, asset, document, event, recollection), with source ID, native ID, and observed time |
| Person | A person candidate with identifiers from contacts and mail. Merging two candidates is a proposal a person approves; it is never automatic |
| Place | A named or located place, from addresses, photo locations, or documents |
| Event | Something that happened or was planned, with participants, place, and a dated interval with precision |
| Claim | A statement about people, places, events, relationships, or periods, with modality (`planned`, `occurred`, `recollected`, `inferred`), citations, producer, and review state |
| Correction | A person's change to a claim or entity, with author and reason |

Schemas are drafted in [interfaces](interfaces.md#4-life-stream-claims).

## Review

Claims start as `proposed`. A person can accept, edit, reject, or mark them `contested`. Accepting a claim captures the cited excerpt and its hash, so the claim stays reviewable if the source changes. The person can also preserve the whole item (for example, an email that is itself a letter worth keeping) as a [preserved artifact](scans-and-destinations.md#preserved-artifacts). Photos and documents held by their own systems are referenced, not copied, unless the person chooses to preserve them.

## Questions with cited answers

A person asks a question such as "When did we first visit Example City?" Worker retrieves accepted claims and granted evidence, sends what the endpoint's data-class grant allows to the bound model, and returns an answer in which every sentence cites a claim or evidence item. Sentences without a citation are flagged as unsupported. An answer can propose new claims, which enter review like any other proposal. Without a model endpoint, the same question runs as a search over claims and evidence.

## Visibility and authorship

Visibility is set from the first release, because a family edition later depends on it.

| Class | Model requests | Exports and shared editions |
| --- | --- | --- |
| `private` | Never sent to any endpoint | Never included |
| `personal` (default) | Allowed within endpoint grants | Only the owner's own exports |
| `shareable` | Allowed within endpoint grants | Eligible for a shared edition after release review |

Visibility applies to sources, individual items, and claims; the most restrictive applicable class wins. Every recollection, correction, and review decision records its author, so a later multi-person household or family edition can show who said what.

## Views

- **Timeline:** accepted and proposed claims on a time axis that shows date precision as ranges.
- **People and places map:** the network of people, places, and events, each linking back to evidence.
- **Item view:** one email, photo, or document with every claim it supports.

## Family edition (long term)

A curated selection of accepted, `shareable` claims and recollections, released after review, exported in an open format with its citations. Not designed in detail yet.
