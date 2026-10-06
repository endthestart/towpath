# ADR 0001: Reference-first digital-life discovery and phased ownership

- Date: 2026-10-05
- Status: Accepted product boundary; delivery priorities confirmed by D20; implementation and live qualification remain pending
- Basis: Owner's seven-answer goal-alignment interview; ownership phases established in answers 1 and 2

## Context

An email-only interpretation loses the broader goal: find information across a digital life, curate and reuse it, then build an evidence-linked timeline and portfolio. Existing file/project discovery, exports and application catalogs contain useful references that should not require another full scan or immediate physical consolidation.

Earlier documents separated Gmail migration from Towpath. The owner has now clarified that durable ownership and eventual source independence belong in the long-term product direction, after discovery and curation. Physical migration and destructive execution still need separate scoped workflows.

## Decision

1. Towpath begins with references, search, curated collections and evidence-linked reuse across connected sources. Mail is the first implementation, not a dependency for every other source.
2. Prefer adapters to existing collectors, importers, indexes and manifests. Keep file discovery optional and separate from mail synchronization.
3. Preserve distinct project identity, content identity, source occurrence, version and verification records. Matches and classifications are reviewable relationships; neither a folder name nor similarity establishes ownership, authorship, a canonical version or an exact duplicate.
4. Record hashes only when computed, including algorithm and what bytes were hashed. Imported verification evidence retains its scope and date. A matching folder copy does not prove complete Git history or recoverable backups.
5. Sequence ownership work: reference/curation; selected durable copies and sync; owner-chosen source independence; physical file/folder organization.
6. Keep physical transfers, provider retirement/deletion, repository repair, NAS access, capacity changes and backup operations in separately authorized operational workflows. This ADR grants none of those actions.
7. Owner decisions and curated relationships are durable; provider indexes and machine classifications are rebuildable. Sensitive/private source material does not become publishable through discovery or AI processing.

## Consequences and specification impact

- [Vision](../vision.md) is the product-scope authority. Architecture and roadmap must describe discovery and reuse alongside mail management and life stream.
- [D3](../decisions.md#d3-first-real-mail-source) still selects Gmail for the implemented first mail connector. [D20](../decisions.md#d20-foundation-before-ai-and-agent-consumers) revises [D9](../decisions.md#d9-life-stream-sources-after-mail) and [D10](../decisions.md#d10-order): Gmail, IMAP and NAS discovery lead, followed by shared search, collections and evidence/agent interfaces. AI processing is a stretch goal. The [foundation specification](../specs/unified-discovery-foundation.md) and [weekly plan](../plans/2026-10-05-local-iteration-week.md) describe the proposed implementation.
- [D17](../decisions.md#d17-file-discovery-boundary) remains the optional-provider boundary for file discovery. The manifest pilot does not adopt a crawler or a second search engine.
- [D4](../decisions.md#d4-archive-input) and [D7](../decisions.md#d7-coverage-report-home) remain withdrawn migration-specific designs. Ordinary archive sources and honest per-source coverage belong in general discovery; durable ownership needs a future preservation contract.
- Common result references, collections and imported verification metadata need specification work. Reuse existing interfaces and stores where they fit; do not select a new storage platform through this ADR.
- Reference-first discovery and curated collections need not wait for optional mail-management integration or model inference.
