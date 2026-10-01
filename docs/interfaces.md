# Interfaces between components

Status: **designed, not built.** Schemas are version 0 drafts. Field names will change during the [first slice](first-slice.md); the boundaries they encode should not change without a design note. All examples are synthetic and use reserved domains (`example.com`, `.test`).

Each record carries a `schema` field so stores can be migrated and so a record crossing a process boundary can be rejected if its version is unknown.

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
  "retention": "full-content"
}
```

`kind` is one of `mail-provider`, `mail-archive`, `calendar-file`, `document`, `media-metadata`. `retention` is one of `metadata-only`, `full-content`, `on-demand` (content fetched when needed and evicted later); the choice is an [owner decision](decisions.md#d5-content-retention).

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
  "first_seen_run": "run_0003",
  "absent_since_run": null,
  "content_ref": "cache:sha256/3b1f…"
}
```

`raw_sha256` covers the exact bytes the source returned. `normalized_sha256` covers decoded headers that survive export (From, To, Cc, Date, Subject, Message-ID) and decoded body parts, so a provider copy and an archive copy can match even when the archive adds headers. The normalization algorithm is part of the schema version.

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

`strength` is one of `exact-raw`, `normalized-content`, `message-id-only`, `conflict` (same Message-ID, different content). A match links occurrences; it never merges them.

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
  "unparseable": [{"native_id": "fx-a-000029", "reason": "invalid date header"}],
  "compared_with": "src_fixture_archive",
  "comparison": {"exact-raw": 0, "normalized-content": 24, "message-id-only": 2, "conflict": 1, "missing": 3},
  "advisory": "Report only. Not an authorization to delete provider mail."
}
```

## 2. Source read API (app reads the source store)

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

A proposal is created by the mail module (rules or a model). It describes one action type against an explicit set of occurrences, with the state that justified it.

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

States: `proposed` → `approved` or `rejected`; `approved` → `executed`, `partially-executed`, `failed`, or `stale`; any open proposal → `expired`. A later sync that changes a target's state makes an unexecuted proposal `stale`.

An approval freezes the proposal by digest. Only a person, through the review UI, creates one.

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

The executor, if one exists, recomputes the digest, refuses action types not on its own allowlist, rechecks each precondition against the provider, and writes a receipt.

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

`result` is one of `applied`, `skipped-precondition`, `skipped-not-allowlisted`, `failed`. Without an executor, an approved proposal can be exported as a checklist for manual action.

## 4. Life evidence and claims

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
     "excerpt_sha256": "77ab…", "captured_excerpt": "Your booking for Example City, 14–17 May, is confirmed."}
  ],
  "producer": "rules/travel-confirmation@0",
  "state": "proposed",
  "uncertainty": "A booking confirmation supports a plan, not that travel occurred."
}
```

`modality` distinguishes `plan`, `occurred`, `recollected`, and `inferred`. `captured_excerpt` is filled when the claim is accepted, subject to the retention policy. A recollection citation points to a recollection version in the life store instead of an occurrence.

## 5. Inference gateway (inside the app)

```text
infer(task, input_parts, data_class, output_schema | none) -> InferenceResult | Disabled(reason) | Failed(reason)
```

The caller names a task (for example `mail.classify`), never an endpoint. The gateway looks up the task binding, checks the endpoint's destination class and data-class grant, picks the structured-output method from the endpoint's capability report, validates the output, and writes a ledger record. See [model providers](model-providers.md). Model output enters the mail store or life store only as a proposal.

## 6. Export for an independent archive or migration

Towpath can export proposals, coverage reports, and accepted claims with citations as JSON Lines using the schemas above. An external tool, including the Gmail evacuation project, may consume these files. Nothing in an export grants authority to act.
