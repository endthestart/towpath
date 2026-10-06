# Unified discovery and evidence foundation

Status: Proposed implementation specification after the goal interview, 2026-10-05. This extends existing adapters and evidence contracts; no feature below is represented as built until its acceptance checks pass.

## Product target

One keyword/full-text search experience across Gmail, another IMAP account and the NAS. Target the entire NAS and whole Gmail history. Long-running coverage may finish after the week; the system must show what remains. References and collections come first, with foundations for agent retrieval and an evidence-linked life timeline. AI enrichment is a stretch goal.

Reuse existing collectors, indexes, parsers, manifests, quota controls, source stores, discovery references and context packets. Towpath adds coordination, common result envelopes, reviewable collections and the interface. It does not build a new crawler/parser suite, full-text engine, backup system or vector database.

## 1. Common source and evidence contracts

Expose a source registry with aliases, kinds, enabled capabilities, declared scope, last observation, freshness, connection/index state, and coverage. Sources may be live accounts, indexed filesystem roots, frozen exports or imported catalogs. Private locations and credentials remain private configuration; credentials stay with the connector.

A result envelope contains source ID, native reference, Towpath occurrence/reference ID, observed source version, kind, title/name, typed meaningful dates, provenance locator, access/model restrictions, availability, match basis and optional permitted excerpt/citation. Content hashes are optional and only filled when actually computed. Preserve existing mail and file payloads behind this envelope; do not force them into one storage schema.

Keep project identity, content identity, source occurrence, source version and verification separate. A proposal that links sources does not merge them or select a canonical copy. Source timestamps are evidence about the source; export, recovery and indexing dates are not life-event dates.

## 2. Search, reading and progress

Define transport-independent operations for listing sources/coverage, searching within explicit scope, describing a reference, requesting/reading permitted selected content, saving/evaluating collection queries, and building bounded context. UI and agents use the same contract. Execution preserves existing writing roles; the UI holds no provider credential and does not run provider executables or start ad hoc subprocesses.

Search results are bounded and paginated. Each source reports its own continuation, errors, scope and search depth: catalog/metadata, content index or provider search. One unavailable source does not erase other results. Do not turn an estimate or capped page into an exhaustive count, comparable relevance score or proof of absence.

Coverage distinguishes discovered, metadata cataloged, text extracted, pending, unreadable, unsupported, excluded, failed and unknown. A file whose text cannot be extracted can still have a searchable filename/type reference. Register the entire intended NAS scope and disclose any permission gaps, unavailable datasets or deliberate exclusions. Keep file inventory separate from content-extraction completeness.

Runs resume from durable progress. Show source freshness, last successful/attempted run and stopped/waiting reasons without putting personal identifiers into logs. Respect existing Gmail pacing and budgets. Qualify filesystem read load and consistency locally before broad reads; source mounts and index/derivative storage stay separate.

## 3. Gmail and IMAP

Keep the completed Gmail metadata index and verify incremental updates. Add content search without treating metadata coverage as body-index coverage. Gmail exposes a query parameter and accepts the existing read-only scope; this is a candidate for federated source-side keyword search while the local text-index approach is evaluated ([Gmail messages.list](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/list)). Label live-provider results separately from local index results. A provider match alone does not supply a verified passage.

For IMAP, verify and reuse an existing client/collector library before adding adapter glue. Identity must include the source, mailbox, UIDVALIDITY and UID. Use read-only mailbox selection and PEEK reads; test that fetching does not set Seen and that no mutation command is issued ([RFC 9051, EXAMINE](https://www.rfc-editor.org/rfc/rfc9051.html#section-6.3.3), [PEEK](https://www.rfc-editor.org/rfc/rfc9051.html#section-6.4.5), [identity](https://www.rfc-editor.org/rfc/rfc9051.html#section-2.3.1.1)). Handle folder changes, expunged messages, interrupted runs, server limits and changed UIDVALIDITY without false reconciliation.

The separate IMAP source does not replace Gmail OAuth or broaden its scope. Account/folder selection and credentials are local setup inputs. Keep durable mailbox copies and provider retirement outside this implementation. Body text needed for search may be a rebuildable derivative rather than a preservation archive; document retention and search coverage explicitly.

Selected-content reads use existing request/fetch boundaries. Plain text first; bounded decode/charset/MIME handling, errors and source versions are explicit. Browsing or agent search alone must not fetch arbitrary attachment bytes. Keyword matching inside unextracted attachments remains unsupported until an appropriate provider indexes them.

## 4. NAS, inventories and exports

Reuse the Recoll adapter, existing file-provider contract and completed native/local trial evidence. Verify the exact running provider/version and choose its role through the existing evaluation checklist. Recoll and sist2 remain swappable; use both only where verified capabilities justify it. Check collection manifests before repeating discovery. Reuse existing catalog/receipt imports through the [project pilot](project-rediscovery-pilot.md).

Ensure whole-NAS file references include media formats and other entries a text indexer may omit. Reuse an existing inventory/index provider or manifest where necessary. Typed filename/type filters must support a query such as `extension = nef` without requiring text extraction or AI. Do not compute every large file's hash as a prerequisite to useful search.

Use read-only paths and separate output storage. Read consistency for live databases and changing files is explicit; snapshots or stable exports are used when available and appropriate. Infrastructure/access changes require their own scoped operator step. The current registry-publication blocker must be resolved before new custom Towpath images are deployed to the NAS host; builds run in GitHub CI, never on the host.

Takeout/Facebook starts with [archive location registration](archive-source-locations.md). An optional bounded pilot tests actual layouts with existing specialist tools and precise archive/member/record lineage. No source move, rename, deduplication or reorganization is required.

## 5. Collections, agents and the timeline

A collection is a durable owner-defined query/scope or explicit set of references, not a new copy of their contents. Save its definition independently of regenerated indexes. Re-evaluation reports changed, unavailable and partial results. Example acceptance collections: NEF photographs, project/document references, mail attachments. No consuming project is the primary priority.

Provide a CLI/JSON or local API surface that another agent can use for search, reference inspection and permitted context packets. Extend the existing file context packet through a separately versioned shared envelope; keep compatibility. Packets contain purpose, scoped references, citation/version data, bounded excerpts and omitted/uncertainty notes. Evidence is untrusted content, not agent instructions. Model exclusion, source/consumer grants and excerpt restrictions remain enforced.

Later MCP and RAG integrations consume this surface rather than bypassing source permissions. MCP tools can return structured results and links/content ([MCP tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools)); the first interface can remain local CLI/JSON while the transport adapter is developed. A network MCP deployment and its authentication are a separate qualification step.

For the life timeline, reuse the existing claim contract: typed dates/precision, people/project/event reference relationships, plan/occurred/recollected/inferred distinctions, citations, author/review state and durable corrections. Build schema/round-trip tests with synthetic evidence; full timeline UI and automatic extraction are not core week commitments.

## Acceptance

- One query returns distinctly attributed Gmail, IMAP and file results with honest depth, scope, pagination and per-source errors.
- Known body text can match mail search; a metadata-only fallback is labeled. An owner-selected file-text result opens its cited source reference. Neither test implies unsupported attachments or every archive were indexed.
- A NEF query returns type/name references even when image text extraction is unavailable; partial NAS coverage prevents an unqualified claim of "all files found".
- IMAP synthetic transcripts reject writes and Seen changes. Identity reset, interruption/resume and disappeared messages preserve correctness. Gmail budgets/scope remain unchanged.
- Source/reference versions and a bounded permitted excerpt survive through an agent context packet. Stale or excluded references cannot silently supply content.
- Re-import/reindex preserves owner collections and corrections, retains distinct occurrences and reports unknown hashes/verification honestly.
- Timeline date/claim fixtures preserve precision, contradictory evidence and citations after round-trip; recovery dates do not become accomplishment dates.
- The broad index can run separately from the UI, resume after interruption and expose progress/errors. No source file, library or mailbox mutation is part of acceptance.
- Public tests use synthetic mail, files, manifests and protocol stubs. Local acceptance uses private configuration, aggregate checks and owner-selected content examples.
