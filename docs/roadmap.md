# Roadmap

This is a dependency map, not a calendar or a claim of completed capabilities.

**Built:** documentation only. **Designed:** everything below. No code, connector, model integration, or mailbox access exists in this repository.

| Milestone | Deliverable | Depends on | Required evidence |
| --- | --- | --- | --- |
| 1. Public foundation | MIT repository, three-concern boundaries, [components](components.md), [interfaces](interfaces.md), [provider contract](model-providers.md), [scans and destinations](scans-and-destinations.md), [decisions](decisions.md) | — | Public tree and history contain no private account or infrastructure data |
| 2. Synthetic read-only mail slice | [First slice](first-slice.md): fixture provider and Maildir connectors, source store with part index, content requests, coverage, deterministic triage, frozen proposals, a read-only scan against a folder destination index, CLI | D2 (accepted) | All first-slice acceptance checks pass; no write path or network access exists |
| 2b. Model endpoint contract | Inference gateway, probes, fallbacks, grants, tested against a loopback stub | 2, D6 wording | Checks 1b-1 to 1b-7 pass |
| 3. First real read-only mail connector | One provider, whole-account read access, index everything and fetch on demand, enforcement level 2 | 2, D3, D5 (accepted) | Private acceptance on the owner's account; public tests remain synthetic |
| 4. Scans and preservation on real mail | Saved selectors, folder destination index, presence reports, preserved artifacts with provenance; no deliveries yet | 3 | Preserved bytes verify against connector hashes; rerunning a scan proposes nothing new |
| 5. Life summary without email | Recollections and an iCalendar import, claims with citations and approximate dates, review, small timeline | 2 (store patterns), D9 | Claims open their evidence; corrections survive reprocessing; no mail source configured |
| 6. Mail as life evidence | Scoped life grant on a mail source; excerpt capture and optional item or attachment preservation; citation re-resolution through an archive match | 4, 5 | A citation survives removal of its provider copy in a synthetic test |
| 7. Action runner | `towpath-act` for approved folder deliveries first; mailbox actions only as chosen in D1; generated filters | 4, D1, D8 | Frozen digests, allowlist, precondition recheck, receipts; mailbox writes additionally need inverse proposals and enforcement level 3 |
| 8. Archive connector for real archives | Manifest support and any tool-specific plugin | 2, D4 | Coverage counts reconcile against the archive tool's own counts |
| 9. More sources and curation | Messages, documents, media metadata, narratives, family export, destination API plugins, and later an archive package built from preserved artifacts | 5, 6 | Per-source grants, release review, portable export and restore |

The owner chose a real mail connector (milestone 3) before life summary without email (milestone 5); milestone 5 depends only on milestone 2, so it can start any time. The personal Gmail evacuation proceeds on its own schedule with its own tools. Towpath's archive connector does not wait for Gmail deletion, and Gmail deletion does not wait for Towpath.
