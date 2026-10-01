# Towpath

Towpath is an open-source project building tools to manage your digital life, starting with email, and using that information to create an evidence-linked life summary.

**Status: designed, not built.** This repository contains architecture documents only. There is no runnable application, mailbox connector, model integration, or archive. Nothing here accesses or changes a live mailbox.

## Three separate concerns

1. **Mail management (Towpath):** help people understand and manage mail that may stay at Gmail or another provider. Towpath reads, classifies, finds items such as attachments, and proposes changes or deliveries to other tools for review. Whether and how Towpath ever executes approved changes is an [open decision](docs/decisions.md#d1-mailbox-and-destination-execution).
2. **Life summary (Towpath):** connect selected sources into reviewable claims about people, events, places, and time, with citations and uncertainty. It works without email.
3. **Personal Gmail evacuation (not Towpath):** one person's project to preserve historical Gmail in a local living archive and possibly remove provider copies. Towpath may read such an archive through an optional [adapter](docs/archive-adapter.md); it never requires the migration.

Each Towpath capability can be deployed alone or together. See [usage modes](docs/components.md#usage-modes).

## Design documents

| Document | Covers |
| --- | --- |
| [Architecture](docs/architecture.md) | Overview, design rules, refinements to the first design |
| [Components](docs/components.md) | Deployable units, store ownership, permission matrix, usage modes |
| [Interfaces](docs/interfaces.md) | Connector, occurrence, grant, proposal, approval, receipt, claim, gateway records |
| [Mailbox actions](docs/mail-boundaries.md) | Action tiers, provider permission limits, execution options compared |
| [Scans and destinations](docs/scans-and-destinations.md) | Generic find-and-route over mail, destination connectors, preserved artifacts |
| [Archive adapter](docs/archive-adapter.md) | Where an optional archive connector fits and which formats it reads |
| [Model providers](docs/model-providers.md) | Endpoint profiles, destinations, data-class grants, capability probes, fallbacks |
| [First slice](docs/first-slice.md) | Smallest read-only synthetic email build and its acceptance checks |
| [Decisions](docs/decisions.md) | Recommendations, open owner decisions, facts to verify |
| [Roadmap](docs/roadmap.md) | Dependency map of milestones |
| [Publication](docs/publication.md) | What belongs in this public repository and what stays private |

## Model providers

Towpath uses explicitly configured OpenAI-compatible endpoints: base URL, credential reference, model, and destination class. Poundlock is one optional endpoint; any compatible local server, gateway, or hosted API can be configured the same way. Towpath probes each endpoint's capabilities with synthetic prompts and falls back only to other methods on the same endpoint or to non-model features. No endpoint is selected automatically, and personal content is never sent to a remote endpoint without an explicit grant. See [model providers](docs/model-providers.md).

## Public project boundary

The public repository holds portable application code, schemas, generic documentation, synthetic fixtures, and release assets. Accounts, mail, photos, credentials, hostnames, network layouts, machine benchmarks, deployment secrets, and personal research stay in each user's private deployment. [Publication rules](docs/publication.md) describe the boundary.

This project is not affiliated with OpenAI, Google, or the authors of the tools it may integrate with. License: [MIT](LICENSE).
