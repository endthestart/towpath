# Towpath product vision

Updated 2026-10-05 from the owner's goal-alignment interview. Outcomes and the phased ownership direction are confirmed; the next delivery plan and source priorities are still being clarified.

## Outcome

One interface to discover a person's digital life, organize and reuse its contents, and build an evidence-linked timeline and portfolio. Email is the first working source. The product spans live accounts, local applications, files, exports, backups and recovered collections.

| Capability | Useful result |
| --- | --- |
| Find | Search across connected sources; rediscover an old document, conversation, photograph, tool or project; trace every result to its location and evidence. |
| Organize and reuse | Curate references and collections, distinguish historical copies from a selected working version, and assemble source-linked material for another application or agent. Resume writing and portfolio creation are example consumers. |
| Tell the story | Connect projects, people, places and events into a timeline with evidence, uncertain dates, visible contradictions and durable owner corrections. |

These capabilities reuse existing specialist tools. Towpath owns their coordination, common references, reviewable relationships, collections and user interface. Connected applications keep their own responsibilities.

## Phased ownership

1. **Reference and curate now.** Index sources where they live, preserve source occurrences and coverage, and build collections from references. Ordinary indexing does not require a full local preservation copy.
2. **Preserve and synchronize later.** Support explicitly selected durable local copies with provenance, periodic updates and restore/export evidence, using suitable existing tools.
3. **Support owner-chosen source independence later.** Let a person evaluate a local source as definitive and understand dependence on a cloud provider. A provider-retirement or deletion workflow needs its own decisions and execution safeguards. Choosing this direction authorizes no transfer or deletion.
4. **Physically organize later.** Propose file/folder organization only after useful indexing and evidence exist; keep execution separate from classification and curation.

See [ADR 0001](adrs/0001-reference-first-digital-life.md). The current UI and connector remain read-only; later phases are product direction, not implemented capabilities.

## Source scope

The source backlog includes Gmail, IMAP accounts, iMessage, file indexes, old mail backups, document/photo libraries, project inventories, Google Takeout, Facebook exports, other messages, contacts and calendars. Their order is open. Named sources are not claims of current support.

A live source and an old export may represent overlapping evidence. Keep occurrences and dates distinct; link possible matches without automatically merging. Unknown format support, unreadable containers and incomplete coverage remain visible.

Use a maintained, suitable existing importer or indexer before writing a new parser. Existing research is dated input: verify the candidate's current API, license, maintenance and behavior before adoption. The public application uses explicitly configured OpenAI-compatible endpoints; Poundlock is an optional provider. Self-hosted AI may classify, connect and summarize permitted evidence while retaining uncertainty, citations and human review.

## Reuse existing discovery work

A project catalog, source index, provenance manifests and verification receipts can become an optional connection without another broad filesystem scan. The [project rediscovery pilot](specs/project-rediscovery-pilot.md) separates project identity, file content, source occurrence, version and verification.

A career evidence packet supplies cited material to a career workflow; approval of career claims remains in that workflow. A photo inventory supplies references to a portfolio workflow; publication remains a separate reviewed step. Backup reports may supply coverage evidence; Towpath does not become a backup engine.

## Planning record

Early private research and architecture notes cover messaging, account exports, archaeology, local inference and life evidence. They are not copied into public documentation because they mix portable findings with private deployment details. Public specs retain generic requirements and synthetic examples. Earlier tool choices are historical evidence, not automatically accepted public dependencies.

The [current week plan](plans/2026-10-05-local-iteration-week.md) is under revision while the interview establishes source priorities and a meaningful demonstration. Mail-management integration remains a capability; the broader discovery workspace does not depend on finishing it first.
