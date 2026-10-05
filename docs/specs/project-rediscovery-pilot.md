# Project rediscovery through existing inventories

Status: Proposed read-only pilot, 2026-10-05. This specifies a connection to existing discovery evidence; it does not authorize transfers, scans, cleanup or implementation before the delivery plan is agreed.

## Purpose

Answer where a project's versions live, which occurrence the owner selected as a working checkout, what historical material could be reused, and what remains remote-only, incomplete or unverified. Later, reviewed relationships can connect projects to career or life-story evidence.

## Fit

Use [file discovery](../file-discovery.md) for precise source references, occurrence/version identity, permissions and coverage. Use [interfaces](../interfaces.md#9-file-discovery-optional) for shared evidence-reference shapes. Extend the specification for project relationships and imported verification receipts rather than forcing project records into mail fields. Follow [ADR 0001](../adrs/0001-reference-first-digital-life.md).

This provider imports an existing inventory. Crawling, archive parsing and full-text extraction remain with existing specialist tools; a searchable catalog alone cannot claim full-content search.

## Inputs and pilot size

Read a bounded selection from the existing project catalog, consolidation status, source index, per-collection provenance manifests, discovery inventory and verification receipts. Use project goals/relationships and infrastructure inventory as context where required to interpret availability. Content retrieval is a separately granted operation.

For an initial pilot, select up to ten existing catalog/provenance entries covering these cases where available: selected working checkout, historical copy, remote-only collection, partially verified copy and an integrity warning. Missing cases remain uncovered in live acceptance; synthetic fixtures cover all cases. No source tree needs to be read to import its manifest.

Public development uses an invented catalog and manifests with reserved names and locations. The local operator supplies private paths through configuration; private source records are never committed or sent to the cloud agent.

## Distinct records

| Record | Meaning |
| --- | --- |
| Project | A reviewable identity and description; owner-selected links to goals and other projects. Similar names do not establish one identity. |
| Source occurrence | One collection or file at an original location, optionally with a recovered-copy reference and acquisition record. Preserve both locations. |
| Content identity | A digest of specified bytes when actually computed. Record algorithm and scope; unknown is valid. |
| Version | A source-reported or explicitly reviewed version reference. File timestamps and recovery dates are not accomplishment dates. |
| Verification | Dated evidence with method, scope, result and receipt reference. File-copy checks, Git integrity and backup recovery are separate checks. |
| Classification/relationship | A suggestion or owner decision, with author, provenance and review state. Working/current/canonical labels require explicit evidence or selection. |
| Coverage | Declared input scope, imported count, rejected/unknown records and known omissions. A partial inventory does not establish complete source coverage. |

## User-visible workflow

Search project descriptions and catalog metadata in the common discovery interface, with source/type and verification filters. Open a result to follow project → occurrence → original location or recovered location → provenance → qualification receipt. Show unavailable locations as references with an explanation.

Curate a collection of useful project references or an evidence packet for another workflow. Owner decisions remain separate from automatic classification and persist when the inventory is re-imported. The pilot can use provenance and metadata without fetching private file contents or making model calls.

## Acceptance criteria

1. Search an invented project represented at multiple locations. Return distinct occurrences and explain any suggested relationship; never collapse them into a canonical result automatically.
2. A detail view traces each record to its input manifest and provenance/receipt references. A referenced record can be unavailable without silently substituting a different copy.
3. Remote-only, partial, failed, unknown and verified states are distinguishable. File-copy qualification does not hide a separate history-integrity warning.
4. Unknown hashes stay unknown. Collection counts are not presented as counts of distinct projects. Export/acquisition dates are labeled separately from event dates.
5. Re-importing the same manifest produces no duplicate occurrences and preserves owner-selected classifications and collections. Changed manifest versions retain their lineage.
6. Corrupt or unsupported records produce a coverage gap and a useful error; they do not make the entire catalog appear complete. Display whether content search was performed.
7. Tests reject source mutations; importing does not execute recovered scripts, Git hooks, settings or repository repair. Local acceptance confirms originals and active working trees are unchanged.
8. Public fixtures, screenshots and logs contain only synthetic values. Private acceptance uses bounded metadata/provenance checks rather than inspecting historical source contents.

## Separate owner decisions

The physical consolidation workstream still needs a choice between source/docs-first recovery and a larger destination for complete folder copies. Storage capacity, permissions, transfers, exclusions, backup recovery and offsite protection belong there. This pilot can reference existing evidence while that decision remains open.

Within Towpath, source priorities, the first common-search experience, evidence-packet consumers and the next implementation slice remain interview decisions. Provider/model adoption and publication of recovered material need their own evidence and review.
