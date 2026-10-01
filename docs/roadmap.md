# Roadmap

This is a dependency map, not a calendar or a claim of completed capabilities.

**Built:** documentation only. **Designed:** everything below. No code, connector, model integration, or mailbox access exists in this repository.

| Milestone | Deliverable | Depends on | Required evidence |
| --- | --- | --- | --- |
| 1. Public foundation | MIT repository, three-concern boundaries, [components](components.md), [interfaces](interfaces.md), [provider contract](model-providers.md), [decisions](decisions.md) | — | Public tree and history contain no private account or infrastructure data |
| 2. Synthetic read-only mail slice | [First slice](first-slice.md): fixture provider and Maildir connectors, source store, coverage, deterministic triage, frozen proposals, CLI | D2 | Acceptance checks 1–13 pass; no write path or network access exists |
| 2b. Model endpoint contract | Inference gateway, probes, fallbacks, grants, tested against a loopback stub | 2, D6 wording | Checks 1b-1 to 1b-7 pass |
| 3. Life summary without email | Recollections and an iCalendar import, claims with citations and approximate dates, review, small timeline | 2 (store patterns), D9 | Claims open their evidence; corrections survive reprocessing; no mail source configured |
| 4. First real read-only mail connector | One provider, enforcement level 2, content retention policy | 2, D3, D5 | Private acceptance on the owner's account; public tests remain synthetic |
| 5. Mail as life evidence | Scoped life grant on a mail source; capture on accept; citation re-resolution through an archive match | 3, 4 | A citation survives removal of its provider copy in a synthetic test |
| 6. Optional mailbox actions | The option chosen in D1 for tier 1 actions; generated filters | 4, D1, D8 | Frozen digests, allowlist, precondition recheck, receipts, inverse proposals, enforcement level 3 |
| 7. Archive connector for real archives | Manifest support and any tool-specific plugin | 2, D4 | Coverage counts reconcile against the archive tool's own counts |
| 8. More sources and curation | Messages, documents, media metadata, narratives, family export | 3 | Per-source grants, release review, portable export and restore |

Milestones 3 and 4 can proceed in either order (D10). The personal Gmail evacuation proceeds on its own schedule with its own tools. Towpath's archive connector does not wait for Gmail deletion, and Gmail deletion does not wait for Towpath.
