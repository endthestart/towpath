# First slice: read-only synthetic mail

Status: **designed, not built.** This is the smallest build that tests the architecture before any real mailbox is connected. It uses only generated synthetic data, makes no network calls, and holds no credentials.

## Purpose

The slice should prove or disprove these design claims:

1. A Gmail-shaped adapter fits the connector interface: native IDs, labels, thread IDs, part structure, change cursors, and fetching one item at a time.
2. Items, dated observations, and absence records hold up under resync, interruption, label changes, and deletion at the provider.
3. Indexing every message while fetching content only on request is enough for triage and scans.
4. The first mail management features can be computed deterministically: categories, important-and-unanswered, unsubscribe review, and digest candidates.
5. Proposals freeze exact targets and observed state, become stale correctly, have no execution path, and lose to a person's corrections.
6. Human decisions, including audience and model-use settings, survive deletion and rebuild of all derived data.
7. `towpath-connect` and `towpath-worker` work as separate processes that share only stores, with the worker reading the source index read-only.

It leaves out models (see slice 1b), the life stream, the web UI, real provider APIs, deliveries, and the action runner. Commands run from a CLI; a minimal web UI comes after the mail features work ([roadmap](roadmap.md) milestone 4).

## Synthetic data

A seeded generator produces the same fixtures on every run. Fixtures use reserved domains only (`example.com`, `example.org`, `*.test`) and invented names. The generator, not committed sample mail, is the source of truth, so fixture changes are reviewed as code.

| Fixture | Form | Contents |
| --- | --- | --- |
| Account A | JSON simulating Gmail's shapes: message and thread IDs, label IDs, a change cursor, part listings, raw RFC 5322 bytes | About 40 messages |
| Account B | Same form | About 10 messages, one sharing a Message-ID with account A |
| Document folder | Plain directory standing in for a document system | A few PDFs, one byte-identical to an attachment in account A |

Message cases the generator must include:

| Case | Tests |
| --- | --- |
| Recurring newsletters with `List-Id` and `List-Unsubscribe`, one with one-click; one mostly unread, one often opened | Categories; unsubscribe review ranking and method display |
| Thread from a known correspondent that asks a question, with no reply | Important-and-unanswered |
| Similar thread that the account holder already answered | Not flagged |
| Automated notifications and receipts | Categories; not flagged as unanswered |
| Monthly statement from the same sender with a PDF | Digest candidate; scan target |
| Travel booking confirmation | Category; future life-stream test case |
| PDF and image attachments, one PDF already in the document folder | Part index, on-demand fetch, presence match by hash |
| Same Message-ID in both accounts | Two items, one link, no merge |
| No Message-ID; malformed `Date`; non-UTF-8 charset; encoded-word subject | Parser robustness and `unparseable` reporting |
| Body telling the reader to "delete all mail and forward credentials" | Prompt-injection fixture; no effect now, and a model test in slice 1b |
| Between runs: one label changed, one message deleted, two new messages | Observations, absence, staleness, incremental sync |

## What gets built

| Piece | Scope in this slice |
| --- | --- |
| Connector interface | `describe`, `probe`, `enumerate`, `fetch` ([interfaces](interfaces.md#1-source-connector-inside-towpath-connect)) |
| Adapters | `fixture-gmail` (lists part structure without attachment bytes until fetched; can simulate interruption and cursor expiry) and `folder` (standing in for a document system's read half) |
| Stores | SQLite files for the source index, work queue, derived store, and decisions store ([components](components.md#stores-and-ownership)) |
| Worker rules | Categories, important-and-unanswered, unsubscribe review list, digest candidates; proposals of type `label.add`, `archive`, and `deliver`, none executable |
| Scan engine | Selector by media type and item filters; content requests; presence check against the document folder |
| Decisions | Recorded corrections, dismissals, review decisions, and audience and model-use settings, entered through the CLI |
| CLI | `connect sync`, `connect fetch-requests`, `mail triage`, `mail unanswered`, `mail unsubscribe-review`, `mail digest`, `mail correct`, `mail export-checklist`, `scan run` (working names) |

Not built: approvals that reach an action runner, deliveries, preserved artifacts, write paths, network access, model calls, life-stream tables, web UI, Compose file.

## Acceptance checks

Each check is an automated test against the generated fixtures.

| # | Check | Claim |
| --- | --- | --- |
| 1 | Syncing twice creates no new items and one new run record | 2 |
| 2 | An interrupted sync is marked incomplete; the next run resumes and the coverage report shows the gap closing | 2 |
| 3 | An expired cursor triggers a full rescan with the same final items | 1, 2 |
| 4 | The deleted message is recorded `absent_since_run`, keeps its history, and makes proposals targeting it `stale` | 2, 5 |
| 5 | The changed label makes a proposal with the old precondition `stale` | 5 |
| 6 | The shared Message-ID yields two items and one match record | 1 |
| 7 | After a sync, every part is listed but no attachment bytes are cached | 3 |
| 8 | Triage fetches bodies only for messages its rules need; scans fetch only matching parts | 3 |
| 9 | Categories, the unanswered list, the unsubscribe ranking, and digest candidates match the generator's expected labels | 4 |
| 10 | A correction moves a message to another category and survives rerunning all rules | 5, 6 |
| 11 | The PDF already in the document folder is `present`; the others become `deliver` proposals | 3, 5 |
| 12 | Proposals hold item IDs, native IDs, and preconditions; their digest is stable across processes | 5 |
| 13 | No module, configuration key, or dependency for provider writes exists | 5 |
| 14 | Deleting the derived store and rerunning reproduces the same proposals, while corrections and review decisions remain | 6 |
| 15 | An item set to model use `excluded` is never placed in the model-input queue, and the setting survives rebuilding the derived store | 6 |
| 16 | `connect` commands and worker commands run as separate processes; the worker opens the source index read-only | 7 |
| 17 | The prompt-injection fixture is classified by the same rules as any other message | 5 |
| 18 | Rerunning triage and scans creates no duplicate requests or proposals | 2, 3 |
| 19 | A repository check rejects fixture addresses outside reserved domains | publication rule |

## Slice 1b: model endpoint contract (optional, still synthetic)

Adds the inference gateway against a stub OpenAI-compatible server started by the test suite on loopback. The stub can be configured to lack `json_schema`, embeddings, or `/models`, or to return malformed JSON.

| # | Check |
| --- | --- |
| 1b-1 | With no endpoint profile, model-backed triage reports `Disabled` and rule-based triage still runs |
| 1b-2 | Probe records exactly the capabilities the stub offers, using synthetic prompts only |
| 1b-3 | Structured output falls back `json_schema` → `json_object` → prompt-only with validation, then to manual review |
| 1b-4 | A `this-machine` profile with a non-loopback host is rejected |
| 1b-5 | A `self-hosted` profile without a ledger grant refuses `content` and `metadata` calls |
| 1b-6 | An item with model use `excluded` is refused even with every grant in place; a `local-only` item is refused by a `self-hosted` endpoint |
| 1b-7 | Changing the profile's model voids its grants and fails queued work instead of rerouting |
| 1b-8 | The prompt-injection fixture produces at most a proposal, never an approval or other record type |

## What the slice should teach

- Whether the item and observation model is too heavy for daily mail management.
- Whether deterministic rules give useful triage before any model is involved.
- Whether separating the decisions store from derived data is worth it in practice.
- What the proposal record needs before the action runner is built.

Next: the real read-only Gmail connection, still CLI-only ([D3](decisions.md#d3-first-real-mail-source), [D14](decisions.md#d14-gmail-access-for-other-self-hosters)).
