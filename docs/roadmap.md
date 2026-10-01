# Roadmap

This is a dependency map, not a calendar or a claim of completed capabilities.

**Built and tested on synthetic data or stubs:** milestones 2 and 2b; the code for milestone 4; the read halves and lookups for milestone 5; and the read-only provider adapter for milestone 3. **Not yet run against any real service.** The next step is local: [local quickstart](setup/local-quickstart.md), with a [handoff prompt](setup/local-agent-handoff.md) for a local agent. **Designed only:** the action runner, web UI, Compose file, and life stream.

| Milestone | Deliverable | Depends on | Required evidence |
| --- | --- | --- | --- |
| 1. Public foundation | Architecture, [services](components.md), [interfaces](interfaces.md), [integration candidates](integrations.md), [tooling review](research/2026-10-tooling-review.md), [decisions](decisions.md) | — | Public tree and history contain no private data or infrastructure details |
| 2. Synthetic read-only slice (**built**) | [First slice](first-slice.md): Gmail-shaped fixture adapter, source index, content requests, scans with presence checks against a folder destination, delivery proposals that cannot execute, decisions store, CLI | D2 | All first-slice checks pass; no write path or network access exists |
| 2b. Model endpoint contract (**built**) | Inference gateway, probes, fallbacks, grants, model-use filtering, against a loopback stub | 2 | Slice 1b checks pass |
| 3. Mail management through Inbox Zero (**read-only adapter built; evaluation pending, local**) | [Evaluation](evaluations/inbox-zero-plan.md) on a dedicated test mailbox, then the Compose `mail` profile (or an existing instance), setup guide for separate Google Cloud projects, every model role configured, provider adapter for statistics, rules, and links | D14, D15 | Points marked "to verify" in [mail management](mail-management.md#integration-points) are answered; evaluation notes stay private, a generic summary goes public |
| 4. Towpath's read-only Gmail connector (CLI) (**built; local verification pending**) | `gmail.readonly` with the person's own OAuth client; incremental, resumable index, newest first; fetch on demand | 2, D3, D14 | Private acceptance on the owner's account; public tests stay synthetic |
| 5. Attachment routing and front end (**read halves and lookups built; action runner, UI, Compose not built**) | Read connectors for a document system and photo library; scans; `towpath-act` deliveries; then a minimal login and overview UI (with links into the mail-management tool) and the Compose default profile | 4, D13, verified [candidates](integrations.md) | Frozen approvals, allowlist, presence rechecks, receipts; reruns propose nothing new |
| 6. Life stream from mail | Mail life grant, people and event candidates, claims with citations and EDTF dates, review, timeline, excerpt capture and optional preservation, recollections, questions with cited answers when a model is bound | 4, 2b for answers | Claims open their evidence; contradictions shown; corrections survive model changes |
| 7. More life sources | Contacts, then calendars, then photo library and document system as evidence sources (references, not copies) | 6, D9 | Each source works with mail disconnected |
| 8. Later | Other mail providers, alternative mail-management providers or Towpath-built replacements, more sources, curated family edition, export packages | 7 | Per-source grants, audience enforced in shared editions, portable export and restore |

Mail management is available from milestone 3 through the integrated tool. The life stream starts from mail in milestone 6 but must work with any single source. Moving mail out of a provider is not on this roadmap; it is a separate project outside Towpath.
