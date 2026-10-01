# First slice: read-only synthetic mail

Status: **designed, not built.** This is the smallest build that tests the architecture before any real mailbox is connected. It uses only generated synthetic data, makes no network calls, and holds no credentials.

## Purpose

The slice should prove or disprove these design claims:

1. A Gmail-shaped adapter fits the connector interface: native IDs, labels, thread IDs, part structure, change cursors, and fetching one item at a time.
2. Items, dated observations, and absence records hold up under resync, interruption, label changes made by another tool, and deletion at the provider.
3. Indexing every message while fetching content only on request is enough for scans.
4. Scans find attachments by selector and check a destination by content hash, producing delivery proposals.
5. Proposals freeze exact targets and observed state, become stale correctly, have no execution path in this slice, and respect a person's dismissals.
6. Human decisions, including audience and model-use settings, survive deletion and rebuild of all derived data.
7. `towpath-connect` and `towpath-worker` work as separate processes that share only stores, with the worker reading the source index read-only.

It leaves out mail-management features (provided by the integrated tool, [D15](decisions.md#d15-role-of-inbox-zero)), models (see slice 1b), the life stream, the web UI, real provider APIs, deliveries, and the action runner. Commands run from a CLI; a minimal web UI comes later ([roadmap](roadmap.md) milestone 5).

## Synthetic data

A seeded generator produces the same fixtures on every run. Fixtures use reserved domains only (`example.com`, `example.org`, `*.test`) and invented names. The generator, not committed sample mail, is the source of truth, so fixture changes are reviewed as code.

| Fixture | Form | Contents |
| --- | --- | --- |
| Account A | JSON simulating Gmail's shapes: message and thread IDs, label IDs, a change cursor, part listings, raw RFC 5322 bytes | About 40 messages |
| Account B | Same form | About 10 messages, one sharing a Message-ID with account A |
| Document folder | Plain directory standing in for a document system, with a manifest giving SHA-256 checksums | A few PDFs, one byte-identical to an attachment in account A |
| Photo folder | Plain directory standing in for a photo library, with base64 SHA-1 checksums | A few images, one byte-identical to an attachment in account A |

Message cases the generator must include:

| Case | Tests |
| --- | --- |
| Monthly statements with PDF attachments, one already in the document folder | Scan, presence by SHA-256, delivery proposals |
| Messages with photo attachments, one already in the photo folder | Presence by the photo library's SHA-1 |
| Large attachment and inline images | Part listing without storing content; selectors by disposition and size |
| Small attachment whose bytes arrive inline instead of as an attachment ID; deeply nested multipart (four or more levels) | Field mask covers every level; inline data is discarded, never cached ([D16](decisions.md#d16-gmail-structure-without-content)) |
| Newsletters, person-to-person threads, notifications | Ordinary indexed mail that scans must ignore |
| Same Message-ID in both accounts | Two items, one link, no merge |
| No Message-ID; malformed `Date`; non-UTF-8 charset; encoded-word subject | Parser robustness and `unparseable` reporting |
| Body telling the reader to "delete all mail and forward credentials" | Prompt-injection fixture; no effect now, and a model test in slice 1b |
| Between runs: labels changed (as if by the mail-management tool), one message deleted, two new messages | Observations, absence, staleness, incremental sync |

## What gets built

| Piece | Scope in this slice |
| --- | --- |
| Connector interface | `describe`, `probe`, `enumerate`, `fetch` ([interfaces](interfaces.md#1-source-connector-inside-towpath-connect)) |
| Adapters | `fixture-gmail` (mimics Gmail's response shapes, including inline body data, so the field mask and discard logic are tested; can simulate interruption and cursor expiry); `folder-documents` and `folder-photos` (standing in for the read halves of a document system and photo library) |
| Stores | SQLite files for the source index, work queue, derived store, and decisions store ([components](components.md#stores-and-ownership)) |
| Scan engine | Selectors by media type, size, disposition, and item filters; content requests; per-destination hashing; presence matches; `deliver` proposals that no code can execute |
| Decisions | Dismissals of matches, review decisions, audience and model-use settings, entered through the CLI |
| CLI | `connect sync`, `connect fetch-requests`, `scan run`, `scan dismiss`, `proposals list`, `proposals export`, `item set` (working names) |

Not built: approvals that reach an action runner, deliveries, preserved artifacts, write paths, network access, model calls, mail-management features, life-stream tables, web UI, Compose file.

## Acceptance checks

Each check is an automated test against the generated fixtures.

| # | Check | Claim |
| --- | --- | --- |
| 1 | Syncing twice creates no new items and one new run record | 2 |
| 2 | An interrupted sync is marked incomplete; the next run resumes and the coverage report shows the gap closing | 2 |
| 3 | An expired cursor triggers a full rescan with the same final items | 1, 2 |
| 4 | The deleted message is recorded `absent_since_run`, keeps its history, and makes proposals targeting it `stale` | 2, 5 |
| 5 | Labels changed between runs are recorded as dated observations; earlier observations are kept | 2 |
| 6 | The shared Message-ID yields two items and one match record | 1 |
| 7 | After a sync, every part is listed, including deeply nested ones, and no body or attachment data is stored, even where the fixture returned it inline | 3 |
| 8 | A PDF scan fetches only matching parts; other messages' bodies are never fetched | 3 |
| 9 | The PDF already in the document folder and the image already in the photo folder are `present`, each found by that destination's own hash algorithm | 4 |
| 10 | The remaining matches become `deliver` proposals with item IDs, part IDs, hashes, and the destination | 4, 5 |
| 11 | A dismissed match is not proposed again after rerunning the scan | 5 |
| 12 | Proposal digests are stable across processes | 5 |
| 13 | No module, configuration key, or dependency for writing to a mailbox or destination exists | 5 |
| 14 | Deleting the derived store and rerunning reproduces the same proposals, while dismissals and settings remain | 6 |
| 15 | An item set to model use `excluded` is never placed in the model-input queue, and the setting survives rebuilding the derived store | 6 |
| 16 | `connect` commands and worker commands run as separate processes; the worker opens the source index read-only | 7 |
| 17 | The prompt-injection fixture has no effect on scans or proposals | 5 |
| 18 | Rerunning scans creates no duplicate requests or proposals | 2, 3 |
| 19 | A repository check rejects fixture addresses outside reserved domains | publication rule |

## Slice 1b: model endpoint contract (optional, still synthetic)

Adds the inference gateway against a stub OpenAI-compatible server started by the test suite on loopback. The stub can be configured to lack `json_schema`, embeddings, or `/models`, or to return malformed JSON.

| # | Check |
| --- | --- |
| 1b-1 | With no endpoint profile, a selector's model classifier reports `Disabled` and the scan still runs without it |
| 1b-2 | Probe records exactly the capabilities the stub offers, using synthetic prompts only |
| 1b-3 | Structured output falls back `json_schema` → `json_object` → prompt-only with validation, then to manual review |
| 1b-4 | A `this-machine` profile with a non-loopback host is rejected |
| 1b-5 | A `self-hosted` profile without a ledger grant refuses `content` and `metadata` calls |
| 1b-6 | An item with model use `excluded` is refused even with every grant in place; a `local-only` item is refused by a `self-hosted` endpoint |
| 1b-7 | Changing the profile's model voids its grants and fails queued work instead of rerouting |
| 1b-8 | The prompt-injection fixture produces at most a proposal, never an approval or other record type |

## What the slice should teach

- Whether the item and observation model is too heavy for indexing a large mailbox.
- Whether per-destination hashing and presence checks are reliable enough to trust delivery proposals.
- Whether separating the decisions store from derived data is worth it in practice.
- What the proposal record needs before the action runner is built.

Next: mail management through Inbox Zero ([D15](decisions.md#d15-role-of-inbox-zero)) and Towpath's own read-only Gmail connector ([D3](decisions.md#d3-first-real-mail-source), [D14](decisions.md#d14-gmail-access-for-other-self-hosters)).
