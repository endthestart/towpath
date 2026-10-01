# Interfaces between services

Status: **designed, not built.** Schemas are version 0 drafts. Field names will change during the [first slice](first-slice.md); the boundaries they encode should not change without a design note. All examples are synthetic and use reserved domains (`example.com`, `.test`).

Each record carries a `schema` field so stores can be migrated and so a record crossing a service boundary can be rejected if its version is unknown.

## 1. Source connector (inside `towpath-connect`)

A connector is a read-only plugin. It never receives a write credential and never calls a model.

```text
describe()                -> SourceDescriptor
probe()                   -> capability list, or a reason the source is unavailable
enumerate(cursor | none)  -> stream of ItemObservation, next cursor, completeness flag
fetch(native_id)          -> raw bytes or structured record, plus content hash
```

A run that ends early records `complete: false`; the coverage report shows the gap until a later run completes. Cursors can expire (provider incremental-sync tokens often do), so every connector needs a full-rescan path.

### SourceDescriptor

```json
{
  "schema": "towpath.source/0",
  "source_id": "src_fixture_a",
  "kind": "mail-provider",
  "connector": "fixture-provider/0",
  "display_name": "Synthetic account A",
  "authority": "read",
  "capabilities": ["enumerate", "fetch_raw", "labels", "incremental_cursor"],
  "retention": "on-demand"
}
```

`kind` is one of `mail-provider`, `mail-archive` (an optional local archive, treated like any other source), `contacts`, `calendar`, `document-system`, `photo-library`. Document-system and photo-library connectors also tell scans what those tools already hold ([destinations](scans-and-destinations.md#destination-connectors)). Every item's metadata and part structure is always indexed. `retention` controls content: `on-demand` (the default: fetched when a rule, scan, or person needs it, then evictable), `full-content`, or `metadata-only`. See [decision D5](decisions.md#d5-content-retention). Durable copies are a separate, explicit choice ([preserved artifacts](scans-and-destinations.md#preserved-artifacts)).

### Occurrence

One record per item per source. Observed state is a dated observation, not a fact about the present.

```json
{
  "schema": "towpath.occurrence/0",
  "occurrence_id": "occ_0042",
  "source_id": "src_fixture_a",
  "native_id": "fx-a-000042",
  "native_thread_id": "fx-a-t-0017",
  "rfc_message_id": "<weekly-0042@news.example.com>",
  "hashes": {
    "raw_sha256": "3b1f…",
    "normalized_sha256": "9c0d…"
  },
  "dates": {
    "header_date": "Tue, 03 Mar 2026 09:15:00 -0500",
    "header_date_utc": "2026-03-03T14:15:00Z",
    "provider_received_utc": "2026-03-03T14:15:07Z"
  },
  "observed": {
    "run_id": "run_0007",
    "labels": ["INBOX", "Newsletters-Candidate"],
    "unread": true
  },
  "parts": [
    {"part_id": "1", "media_type": "text/plain", "bytes": 2210, "disposition": "inline"},
    {"part_id": "2", "media_type": "application/pdf", "bytes": 48213, "disposition": "attachment",
     "file_name": "issue-42.pdf", "sha256": null}
  ],
  "first_seen_run": "run_0003",
  "absent_since_run": null,
  "content_ref": "cache:sha256/3b1f…"
}
```

`parts` comes from the provider's structure listing where available, without downloading attachments; a part's `sha256` is filled when its bytes are first fetched. `raw_sha256` covers the exact bytes the source returned. `normalized_sha256` covers decoded headers that survive export (From, To, Cc, Date, Subject, Message-ID) and decoded body parts, so copies of one message in different sources (for example two accounts that both received it) can be recognized even when headers differ. The normalization algorithm is part of the schema version.

### Cross-source match

```json
{
  "schema": "towpath.match/0",
  "left": "occ_0042",
  "right": "occ_9042",
  "strength": "normalized-content",
  "basis": ["rfc_message_id", "normalized_sha256"],
  "computed_run": "run_0007"
}
```

Matches also link a candidate attachment to a destination index entry by SHA-256 ([destinations](scans-and-destinations.md#destination-connectors)). `strength` is one of `exact-raw`, `normalized-content`, `message-id-only`, `conflict` (same Message-ID, different content). A match links occurrences; it never merges them.

### Coverage report

```json
{
  "schema": "towpath.coverage/0",
  "source_id": "src_fixture_a",
  "run_id": "run_0007",
  "complete": true,
  "items_seen": 30,
  "items_new": 2,
  "items_absent_since_last_run": 1,
  "unparseable": [{"native_id": "fx-a-000029", "reason": "invalid date header"}]
}
```

### Content request

Web and worker cannot fetch from a source. They ask `towpath-connect` through the work queue:

```json
{
  "schema": "towpath.content-request/0",
  "request_id": "req_0301",
  "occurrence_id": "occ_0071",
  "part_id": "2",
  "requested_by": "scan_0011",
  "priority": "background"
}
```

`towpath-connect` fetches only that occurrence or part, verifies or records its hash, caches it, and marks the request done or failed. Scans process mail one item at a time this way, so read access to a whole account does not mean copying the whole account.

## 2. Source read API (web and worker read the source index)

```text
list_occurrences(consumer, filter) -> occurrences inside the consumer's grant
get_content(consumer, occurrence_id, part) -> content, if retained and granted
resolve(citation) -> current occurrence, a matching occurrence elsewhere, or "unavailable"
```

`consumer` is `mail` or `life`. The read API checks a grant before returning anything:

```json
{
  "schema": "towpath.grant/0",
  "source_id": "src_fixture_a",
  "consumer": "life",
  "scope": {"labels_any": ["Travel"], "date_from": "2025-01-01", "content": "excerpts"},
  "granted_at": "2026-10-01T12:00:00Z"
}
```

A mail source added for mail management gets a `mail` grant. It gets no `life` grant unless a person creates one.

## 3. Mail proposal, approval, and receipt

A proposal is created by worker (rules or a model). It describes one action type against an explicit set of occurrences, with the state that justified it.

```json
{
  "schema": "towpath.mail.proposal/0",
  "proposal_id": "prop_0012",
  "account_source_id": "src_fixture_a",
  "action": {"type": "label.add", "label": "Newsletters"},
  "targets": [
    {"occurrence_id": "occ_0042", "native_id": "fx-a-000042",
     "precondition": {"observed_run": "run_0007", "labels_include": ["INBOX"]}}
  ],
  "reason": {"producer": "rules/list-id@0", "summary": "Recurring list with List-Id news.example.com", "evidence": ["header:List-Id"]},
  "uncertainty": "low",
  "created_at": "2026-10-01T12:05:00Z",
  "expires_at": "2026-10-08T12:05:00Z"
}
```

Towpath's action runner currently allows only `deliver` ([scans and destinations](scans-and-destinations.md#delivery-proposal)); mailbox changes belong to the integrated mail-management tool ([D1](decisions.md#d1-mailbox-and-destination-execution)). The `label.add` example shows the shape a mailbox action would take if Towpath builds its own later.

States: `proposed` → `approved` or `rejected`; `approved` → `executed`, `partially-executed`, `failed`, or `stale`; any open proposal → `expired`. A later sync that changes a target's state makes an unexecuted proposal `stale`.

An approval freezes the proposal: web stores a copy of it and its digest in the decisions store. Only a person, through the review UI, creates one.

```json
{
  "schema": "towpath.mail.approval/0",
  "proposal_id": "prop_0012",
  "proposal_sha256": "e41a…",
  "approved_targets": ["occ_0042"],
  "approved_by": "local-user",
  "approved_at": "2026-10-01T12:10:00Z"
}
```

The action runner (`towpath-act`), where deployed, recomputes the digest, refuses action types not on its own allowlist, rechecks each precondition against the provider, and writes a receipt.

```json
{
  "schema": "towpath.mail.receipt/0",
  "proposal_id": "prop_0012",
  "proposal_sha256": "e41a…",
  "executor": "towpath-act/0",
  "items": [
    {"native_id": "fx-a-000042", "result": "applied", "at": "2026-10-01T12:11:02Z"}
  ]
}
```

`result` is one of `applied`, `skipped-precondition`, `skipped-not-allowlisted`, `failed`. Without the action runner, an approved proposal can be exported as a checklist for manual action.

## 4. Life stream claims

```json
{
  "schema": "towpath.claim/0",
  "claim_id": "clm_0007",
  "kind": "event",
  "statement": "Planned a trip to Example City",
  "modality": "plan",
  "when": {"expression": "mid-May 2026", "earliest": "2026-05-10", "latest": "2026-05-20", "precision": "range"},
  "citations": [
    {"occurrence_id": "occ_0055", "part": "text/plain", "span": [120, 188],
     "excerpt_sha256": "77ab…", "captured_excerpt": "Your booking for Example City, 14–17 May, is confirmed.",
     "preserved": {"level": "item", "artifact_sha256": "a90c…"}},
    {"external": {"source_id": "src_photos", "kind": "photo-library", "native_id": "asset-7f3e"},
     "observed": "taken 2026-05-15, Example City"}
  ],
  "audience": "owner",
  "model_use": "follow-grants",
  "producer": "rules/travel-confirmation@0",
  "state": "proposed",
  "uncertainty": "A booking confirmation supports a plan, not that travel occurred."
}
```

`modality` distinguishes `plan`, `occurred`, `recollected`, and `inferred`. An `external` citation references an item another system owns, such as a photo or document, by its native ID; Towpath does not copy it. `audience` is `owner` or `shareable`; `model_use` is `follow-grants`, `local-only`, or `excluded` ([life stream](life-stream.md#audience-and-model-use)). `captured_excerpt` is filled when the claim is accepted. `preserved` is optional: the person can also keep the whole message (`item`) or one attachment (`part`) as a [preserved artifact](scans-and-destinations.md#preserved-artifacts) when the source itself is valuable. A recollection citation points to a recollection version, with its author, in the decisions store.

## 5. Inference gateway (inside worker)

```text
infer(task, input_parts, data_class, output_schema | none) -> InferenceResult | Disabled(reason) | Failed(reason)
```

The caller names a task (for example `mail.classify`), never an endpoint. The gateway looks up the task binding, checks the endpoint's destination class and data-class grant, picks the structured-output method from the endpoint's capability report, validates the output, and writes a ledger record. Input parts whose model use forbids this endpoint are refused whatever the grants. See [model providers](model-providers.md). Model output enters the derived store only as a proposal.

## 6. Scans, destinations, and preserved artifacts

Selector, delivery proposal, and artifact records are defined in [scans and destinations](scans-and-destinations.md).

## 7. Export

Towpath can export approved proposals, preserved artifacts with provenance, and accepted claims with citations as JSON Lines using the schemas above. The owner's own exports include everything; shared editions include only items and claims with `shareable` audience. Nothing in an export grants authority to act.
