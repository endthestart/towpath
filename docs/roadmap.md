# Roadmap

This is a dependency map, not a calendar or promise of completed capabilities. Current state: design documents only.

| Milestone | Deliverable | Required evidence |
| --- | --- | --- |
| 1. Public project foundation | MIT repository, boundaries, provider contract, synthetic fixtures, contribution guidance | Public tree and history contain no private account or infrastructure data |
| 2. Read-only mail slice | One generic mail read adapter, source coverage report, evidence references, search, and action proposals | Rerun/recovery and missing-item reports; no mailbox write credential |
| 3. Model-assisted mail | Configurable Chat Completions endpoint, deterministic rules, structured extraction, review | Synthetic compatibility and prompt-injection checks; measured quality and cost |
| 4. Life-summary slice | Claims with citations, approximate dates, review, a small timeline and question interface | Claims open their evidence; conflicts and corrections survive reprocessing |
| 5. Optional mailbox actions | Internal executor for explicitly selected action types | Exact reviewed batch, narrow credentials, state recheck, receipts, failure reconciliation |
| 6. More sources and curation | Messages, calendar, media/document references, memory prompts, family export | Per-source permissions, access checks, portable export and restore |

The personal Gmail evacuation project can proceed independently using existing archive tools and its own preservation plan. Towpath's archive read adapter can be specified once the chosen archive format and access contract are known. Its implementation does not need to wait for Gmail deletion, and Gmail deletion does not need to wait for Towpath.

Initial architecture questions for public review: which mail providers and read methods to support first; whether the mail and life-summary capabilities should be deployable separately; which action types belong in Towpath's executor; and which evidence export format serves independent preservation best. These questions do not block publication of this design.
