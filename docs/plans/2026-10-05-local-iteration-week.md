# Foundation week: October 5–9, 2026

Status: Working execution plan after seven goal-alignment questions. Replaces the earlier email-viewer schedule. [Confirmed direction](goal-alignment-2026-10-05.md), [vision](../vision.md), [ADR 0001](../adrs/0001-reference-first-digital-life.md), [implementation specification](../specs/unified-discovery-foundation.md).

## Local checkpoint

The owner chose to continue implementation and integration locally, avoiding frequent agent handoffs. The unified foundation and its review fixes are integrated into the local working branch. A backed-up private-store copy passed the additive upgrade with every existing row preserved; the live upgrade and restarted interface passed. Live Gmail provider search and an explicitly queued keyword search passed through the existing read-only client and pacing, with no content downloaded. Search and collections pages are available in the running local interface.

The existing verified-quota opt-in was recovered from earlier local sync work and retained with its 30%/1,800-unit ceiling. Public regression fixtures are synthetic; account configuration, logs, copies and operational runner stay private. Source setup and feature acceptance continue here. No new cloud handoff is planned. IMAP credentials/server acceptance, NAS provider qualification and broad indexing, export imports, and timeline/context acceptance remain open. These results do not close the remaining Gmail incremental-sync validation gates.

## Friday target

The owner approved moving persistent operation to Hub/Arcane before Fastmail setup or broad
NAS indexing. [Hub setup](../setup/hub.md) defines the first SSH-only deployment, private-store
handover, explicit request worker and manual indexing profiles. Fix and qualify publication,
deploy verified GitHub-built images, then resume source acceptance. Development continues
locally; personal state and source configuration remain private on the deployment host.

A useful cross-source foundation: Gmail and IMAP keyword/content search, NAS indexing working toward complete coverage, one search UI, durable collection queries, and an agent-readable evidence/context interface. Source references and date/claim contracts prepare the life timeline. Full NAS and Gmail coverage is the target; long extraction runs may continue beyond Friday with explicit progress and gaps. AI processing is a stretch goal.

The Gmail metadata index and local UI already work. A Recoll adapter, file-context packets and bounded native/local trials exist. IMAP, the unified UI/service and shared collection/evidence interfaces require development. This plan builds on those pieces rather than starting another crawler, parser, search engine or inference platform.

## Five-day delivery plan

| Day | Development and integration | Local work and daily proof |
| --- | --- | --- |
| **Monday, October 5 — Contracts and execution lanes** | Finish the goal record, source/evidence/search contracts and acceptance fixtures. Establish common source registry, result envelope, typed filters, coverage and provider capability model. Prepare GitHub-only development handoffs. Triage the container publication blocker in a separate release lane. | Map existing Gmail, file-index and project-catalog work to the contracts. Identify the IMAP connection setup and intended NAS roots, existing indexes, read paths, output capacity and current read-load/consistency constraints. Demonstrate a synthetic query across the three source kinds with partial/unavailable states. |
| **Tuesday, October 6 — Broad NAS indexing** | Connect existing file/inventory providers through the common interface; integrate project/provenance manifests. Add restartable progress/coverage and file type/path/date filters. Include metadata references for formats a text provider cannot extract. | Qualify the read-only paths and exact provider build, keep derivatives separate, then launch indexing across the intended NAS scope at a measured sustainable pace. Demonstrate source tracing and a NEF collection query. Use GitHub-built/published images for custom host deployment; release failure is a visible gate, never a reason for a server build. |
| **Wednesday, October 7 — Mail search and IMAP** | Add the IMAP adapter using a verified existing library/collector. Implement identity/reconciliation, metadata/content search and selected-content reads under the connector role. Extend Gmail search beyond metadata using a verified provider-search or existing local-index path. | Configure the owner's selected IMAP source. Verify read-only behavior, full-history scope and existing Gmail pacing; test incremental changes, restart and remaining validation gates. Demonstrate known body-text searches and explicitly selected content retrieval without changing mail flags or fetching all attachment bytes. |
| **Thursday, October 8 — Unified UI, collections and agent access** | Bring live/file/manifest results into one search view with source/type/date filters, precise references, source freshness and honest coverage. Save collection definitions separately from indexes. Expose the same scoped search/describe/context operations through CLI/JSON or a local API. | Use owner-selected examples across the connected sources. Show an agent-ready NEF or project/document collection with stable references, bounded permitted context and unavailable/stale states. Reindex/restart and prove saved collections survive. |
| **Friday, October 9 — Evidence foundation and end-to-end acceptance** | Complete typed-date and timeline-claim schema/round-trip tests, citation resolution, durable review/correction boundaries and packet versioning. Verify the day's integrated revision and document operational steps and gaps. | Demonstrate search → source/evidence → collection → agent context. Report actual NAS/mail coverage, resume status and unsupported inputs. Register Takeout/Facebook locations; attempt a small import only if the core is ready. Stretch: a local read-only MCP wrapper or a small self-hosted AI enrichment trial after evidence/grant checks. |

Daily ordering is a target, not an instruction to skip prerequisites. If a dependency fails, finish or explicitly report that gate rather than claiming its dependent capability exists. Full index completion, source/provider coverage, body-text coverage and archive parsing are separate measurements.

## Parallel work lanes

- **Cloud development:** common service/contracts, IMAP adapter, unified UI and collection/context interfaces. The [handoff](../setup/unified-discovery-cloud-handoff.md) contains public scope, synthetic fixtures and acceptance. Use a separate branch/PR; do not merge or publish automatically.
- **Local operation and review:** private source configuration, NAS/provider qualification, paced jobs, owner-guided acceptance, small fixes and review of returned development. The cloud agent cannot access local files, accounts, the NAS or deployment hosts.
- **Release qualification:** resolve source-blob upload failures and verify corresponding-source publication before deploying new custom images. This is independent of designing the discovery interfaces; it becomes a dependency for host deployment.

Core integration/evidence work comes before inference. Self-hosted AI uses the existing explicitly configured OpenAI-compatible gateway and grants; it does not require natural-language search to be delivered this week. Embeddings, RAG and richer MCP support build on the retrieval/evidence contracts in later increments.

## Completion checks

1. Gmail, the selected IMAP source and NAS providers are represented with explicit scope and capabilities; no provider is silently replaced by a synthetic one.
2. Keyword/content search and metadata/type search are distinguishable. Source errors, incomplete pages and unextracted content remain visible.
3. Broad NAS indexing is operating or has an explicit actionable gate, with resume/progress and actual coverage reported. Entire-NAS coverage is not inferred from a successful small trial.
4. Results and a saved collection can be consumed through an agent interface with source/version/citation references. An NEF query does not need AI.
5. A timeline fixture preserves date meaning/precision and cited evidence; owner decisions persist independently of regenerated data.
6. Appropriate synthetic tests and local qualification pass, originals stay unchanged, and private data is absent from public commits and handoffs.

A Friday core demo should be live for the connected sources. Anything still synthetic, blocked or waiting on long scans is labeled separately with its next action. A finished polished timeline, natural-language answers, physical consolidation and provider deletion are not core commitments.
