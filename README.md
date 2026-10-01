# Towpath

Towpath is an open-source, self-hosted web application for managing your digital life, starting with email, and for building an evidence-linked life story from the sources you connect.

**Status: designed, not built.** This repository contains design documents only. There is no runnable application, Compose file, connector, or model integration yet, and nothing here accesses or changes a mailbox.

## What it will do

- **Mail management:** unsubscribing, important mail still waiting for your reply, draft replies, categories, and smart rules come from an integrated mail-management tool (Inbox Zero first) running in Towpath's Compose setup with its own Gmail access. Towpath adds routing attachments to your document system or photo library, through a separately permissioned action runner after you approve.
- **Life stream:** find people, events, places, photos, and documents across connected sources and turn them into reviewable claims with citations, uncertain dates, and visible contradictions. Ask questions and get cited answers. In the long term, curate a story to share with family. It starts from mail but works with any source.

## How it is built

Towpath is the front end and coordinator. It connects to tools people already run (for example a document system or photo library) or bundles open-source tools in its Docker Compose file, and writes code only where nothing suitable exists. Everything Towpath uses is open source and free for personal use; components with personal-use-only terms stay optional ([license policy](docs/integrations.md)). See [integrate first](docs/architecture.md#integrate-first).

Model features use explicitly configured OpenAI-compatible endpoints: base URL, credential, model, and destination. Poundlock is one optional endpoint; a bundled local model server or any compatible API works the same way. Personal data is not sent to any endpoint outside the machine unless the owner grants it, and items can be kept local-only or excluded from models entirely. See [model providers](docs/model-providers.md).

Moving mail out of a provider is not part of Towpath.

## Design documents

| Document | Covers |
| --- | --- |
| [Architecture](docs/architecture.md) | What Towpath is, integrate-first, design rules, what is out of scope |
| [Services](docs/components.md) | Compose services and profiles, stores, credentials, permission matrix |
| [Mail management](docs/mail-management.md) | Features, action tiers, provider permissions, execution options |
| [Life stream](docs/life-stream.md) | Evidence rule, sources, claims, review, audience and model use, cited answers |
| [Scans and destinations](docs/scans-and-destinations.md) | Finding items in mail, routing them to other tools, preserving evidence |
| [Integrations](docs/integrations.md) | Candidate existing tools and libraries per need, with an evaluation checklist |
| [Interfaces](docs/interfaces.md) | Records that cross service boundaries |
| [Model providers](docs/model-providers.md) | Endpoint profiles, destinations, grants, capability probes, fallbacks |
| [First slice](docs/first-slice.md) | Smallest read-only synthetic email build and its checks |
| [Tooling review, October 2026](docs/research/2026-10-tooling-review.md) | Research on existing tools, licenses, Gmail access rules, and model servers, with sources |
| [Decisions](docs/decisions.md) | Accepted, withdrawn, and open decisions; facts to verify |
| [Roadmap](docs/roadmap.md) | Dependency map of milestones |
| [Publication](docs/publication.md) | What belongs in this public repository and what stays private |

## Public project boundary

The public repository holds portable application code, schemas, generic documentation, synthetic fixtures, and release assets. Accounts, mail, photos, credentials, hostnames, network layouts, and personal research stay in each user's private deployment. [Publication rules](docs/publication.md) describe the boundary.

This project is not affiliated with OpenAI, Google, or the authors of the tools it may integrate with. License: [MIT](LICENSE).
