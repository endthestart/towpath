# Roadmap

This is a dependency map, not a calendar or a claim of completed capabilities.

**Built:** documentation only. **Designed:** everything below. No code, connector, model integration, Compose file, or mailbox access exists in this repository.

| Milestone | Deliverable | Depends on | Required evidence |
| --- | --- | --- | --- |
| 1. Public foundation | Architecture, [services](components.md), [interfaces](interfaces.md), [integration candidates](integrations.md), [decisions](decisions.md) | — | Public tree and history contain no private data or infrastructure details |
| 2. Synthetic read-only mail slice | [First slice](first-slice.md): Gmail-like fixture adapter, source index, content requests, deterministic triage, frozen proposals, a read-only scan against a folder destination, CLI | D2 | All first-slice checks pass; no write path or network access exists |
| 2b. Model endpoint contract | Inference gateway, probes, fallbacks, grants, model-use filtering, against a loopback stub | 2 | Slice 1b checks pass |
| 3. Real Gmail connection, read-only (CLI) | Gmail OAuth with the person's own client, whole-mailbox index with fetch on demand, run as separate connect and worker processes | 2, D3, D14 | Private acceptance on the owner's account; public tests stay synthetic |
| 4. Mail management review | Categories with corrections, important-and-unanswered list, unsubscribe review, draft text to copy, digest and rule previews, search; read connections to a document system and photo library for presence checks; then a minimal login and review UI and the Compose default profile | 3, D8, D12, D13, verified [candidates](integrations.md) | Corrections survive reprocessing; rerunning produces no duplicate proposals |
| 5. Action runner | `towpath-act` for tier 1 mailbox changes and deliveries to a folder, document system, or photo library | 4, D1 | Frozen approvals, allowlist, rechecks, receipts, inverse proposals, dry run |
| 6. Life stream from mail | Mail life grant, people and event candidates, claims with citations and date precision, review, timeline, excerpt capture and optional preservation, recollections, questions with cited answers when a model is bound | 3 (4 helpful), 2b for answers | Claims open their evidence; contradictions shown; corrections survive model changes |
| 7. More life sources | Contacts, then calendars, then photo library and document system as evidence sources (references, not copies) | 6, D9 | Each source works with mail disconnected |
| 8. Later | Other mail providers (IMAP, JMAP), more sources, curated family edition, export packages | 7 | Per-source grants, audience enforced in shared editions, portable export and restore |

Mail management can be used on its own from milestone 3 or 4. The life stream starts from mail in milestone 6 but must work with any single source. Moving mail out of a provider is not on this roadmap; it is a separate project outside Towpath.
