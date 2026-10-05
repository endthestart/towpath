# Archive sources: register locations, derive indexes separately

Status: Proposed phase-one approach, 2026-10-05. Takeout/Facebook is an optional pilot beside the week's core Gmail, IMAP and file indexing work. No importer or archive format is claimed as implemented through this specification.

## User outcome

Connect an existing export or backup in place, find its supported contents through Towpath, and follow a result back to the export, member and record that supplied the evidence. Setup should not require reorganizing an archive collection.

## Source registration

Describe the source with an owner-chosen alias, a private root or archive location, source/export type, known export date and a scope statement. Record a logical export's parts when multiple files belong together. Keep source locations in private deployment configuration.

An export is a snapshot, not a live account. Import it once into the searchable catalog and record the observed source version; repeat safely if interrupted. A newly supplied export is a new snapshot with its own lineage. A changing file folder has a refresh policy and dated coverage. Neither is represented as a complete live-account mirror.

## Processing flow

1. **Describe the input.** Locate manifests and supported export layouts without modifying originals. Identify whether the input is a directory, archive, split export or nested backup. Unknown layout is a reported result.
2. **Read through a specialist provider.** Prefer an existing maintained indexer/importer for document/mail text and structured export records. Verify support for the actual export generation and format; a generic filename match does not prove records were parsed.
3. **Keep derivatives separate.** Indexes, extracted text, structured records, temporary extraction and model annotations live outside source roots. If a parser requires unpacking, unpack a bounded selected input into separate scratch/derivative storage and retain its archive-member lineage.
4. **Expose precise evidence.** Results preserve source alias, original occurrence, member/record locator, parser version, extraction status, meaningful dates and any actually computed hashes. Messages, events and media references retain their own record types.
5. **Report scope honestly.** Distinguish cataloged names, content-indexed records, truncated/skipped/encrypted/unsupported items, errors and unexamined inputs. A one-time export import is complete only for its declared readable scope.

Selected, permitted evidence can later supply cited model answers and life-stream proposals through existing policy. The index itself does not approve a statement as a fact or make private material shareable.

## Pilot acceptance

- One invented Takeout-style fixture and one invented Facebook-style fixture provide a declared set of records. Supported records are searchable and traceable to their original members/IDs.
- Unsupported variants, missing split parts, unreadable members and interrupted runs remain visible as coverage gaps.
- An unchanged re-import preserves occurrence identity and creates no duplicate catalog records; a changed export retains its separate provenance.
- Export/acquisition/index dates are distinguished from the dates of messages or events.
- Source hashes or an equivalent bounded fixture check prove originals were unchanged. Outputs stay outside source roots. Local acceptance uses a small owner-selected archive and generic public findings only.

## Later ownership work

A person may later choose a durable preservation copy, synchronization or physical organization under [ADR 0001](../adrs/0001-reference-first-digital-life.md). Those workflows reuse the references and provenance established here, with separate qualification and execution decisions.
