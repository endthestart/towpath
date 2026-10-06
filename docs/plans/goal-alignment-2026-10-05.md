# Goal-alignment record, October 5, 2026

Status: Interview completed after seven questions; the confirmed direction is sufficient for a working weekly plan. This record keeps portable answers and open decisions; private catalogs, infrastructure and the original mixed-project handoffs remain outside Git.

## Confirmed answers

1. **Recurring outcomes:** one interface to find a digital life; organize and own its content for reuse in resumes, portfolios, rediscovery and other workflows; build an evidence-linked life timeline and portfolio.
2. **Ownership sequence:** organize references and collections where sources live first; selected durable local copies with periodic sync next; owner-chosen local source independence later; physical file/folder organization last. The backlog includes messaging, IMAP, files, exports, backups and existing inventories as well as Gmail.
3. **This week's source priorities:** Gmail, IMAP and file-indexing sources are core. A source-location specification or one-time Takeout/Facebook export pilot is optional. Export processing depth is still to decide; existing source locations are the proposed starting point ([archive source specification](../specs/archive-source-locations.md)).
4. **Search experience:** start with simple keyword/full-text search, while retaining the references, source versions, permissions, extraction evidence and citation contracts needed for later natural-language questions. Advanced semantic retrieval and generated answers are later increments.
5. **Coverage target:** the entire NAS and the whole Gmail account, with background indexing and honest progress/coverage rather than a deliberately small final scope. Reuse existing indexes and inventories before scanning missing coverage. IMAP remains a core source; its exact account/folder scope is not yet specified. Complete coverage is a target, not a promise that every format is readable or that every long scan finishes this week.
6. **Reuse priorities:** there is no preferred first consumer. Build the common foundation for curated collections and agent discovery, plus the evidence/date relationships required by a life timeline. Media/archive, career and research/ML workflows are example consumers. A reusable query such as "all NEF files" should return source-linked results without treating a collection as permission to move or copy its contents.
7. **AI and agent direction:** establish source integrations and evidence structures first; self-hosted AI processing/enrichment is a stretch goal. MCP servers, RAG and day-to-day agent access are long-term outcomes. Build a shared scoped search/read/context interface rather than consumer-specific machinery.

The consolidation handoff is planning input. Towpath should reuse manifests, catalogs, provenance and qualification receipts through its discovery interfaces. Project identity, file content, source occurrences and verification stay distinct. Preserve unknown hashes and coverage gaps; classifications and relationships are reviewable. Physical consolidation and infrastructure operations remain separate.

## Evidence and documentation reconciliation

The initial private goals, ten research reports and synthesis plan cover broader sources, life evidence, archive archaeology and self-hosted inference. Their contents are dated; they mix infrastructure details with generic research. They are not suitable for copying wholesale into public Git or handing to a cloud agent. This audit establishes that substantial notes exist, not that every earlier conversation was fully captured.

The public architecture/roadmap had drifted toward an email-first delivery sequence and contained stale statements about the UI. The current [vision](../vision.md), [ADR 0001](../adrs/0001-reference-first-digital-life.md), [D19](../decisions.md#d19-reference-first-discovery-and-phased-ownership) and [manifest pilot](../specs/project-rediscovery-pilot.md) restore the broader product boundary. Tool adoption and performance estimates from early research need current verification before use. The exact earlier presentation diagram has not yet been identified.

The [weekly schedule](2026-10-05-local-iteration-week.md) now replaces the earlier email-viewer schedule with a cross-source foundation sprint, backed by the [foundation specification](../specs/unified-discovery-foundation.md).

## Remaining setup inputs

There is enough product direction to plan without an eighth interview question. IMAP provider/account/folders, exact NAS read paths, current provider/release qualification and sustainable scan load are local setup inputs, to resolve when their operational steps are ready. Complete coverage remains a target with measured progress, not a guaranteed Friday completion date.
