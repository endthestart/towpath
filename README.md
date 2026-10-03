# Towpath

Towpath is an open-source, self-hosted web application for managing your digital life, starting with email, and for building an evidence-linked life story from the sources you connect.

**Status: read-only command-line tools built and tested on synthetic data and stubs; not yet run against real accounts.** The repository has the design documents and a CLI that can index Gmail read-only (with your own OAuth client), find attachments, check whether Paperless-ngx or Immich already holds them, call explicitly configured model endpoints under policy, read Inbox Zero statistics, and (optionally) search folders, archives, and old mail backups through Recoll. Nothing here can change a mailbox or library. There is no web UI, Compose file, or action runner yet. To try it on your own accounts, follow the [local quickstart](docs/setup/local-quickstart.md).

## What it will do

- **Mail management:** unsubscribing, important mail still waiting for your reply, draft replies, categories, and smart rules come from an integrated mail-management tool (Inbox Zero first) running in Towpath's Compose setup with its own Gmail access. Towpath adds routing attachments to your document system or photo library, through a separately permissioned action runner after you approve.
- **Life stream:** find people, events, places, photos, and documents across connected sources and turn them into reviewable claims with citations, uncertain dates, and visible contradictions. Ask questions and get cited answers. In the long term, curate a story to share with family. It starts from mail but works with any source.

## How it is built

Towpath is the front end and coordinator. It connects to tools people already run (for example a document system or photo library) or bundles open-source tools in its Docker Compose file, and writes code only where nothing suitable exists. Everything Towpath requires is open source; optional components may be source available with use restrictions if they are free for personal self-hosting ([license policy](docs/integrations.md)). See [integrate first](docs/architecture.md#integrate-first).

Model features use explicitly configured OpenAI-compatible endpoints: base URL, credential, model, and destination. Poundlock is one optional endpoint; a bundled local model server or any compatible API works the same way. Personal data is not sent to any endpoint outside the machine unless the owner grants it, and items can be kept local-only or excluded from models entirely. See [model providers](docs/model-providers.md).

Moving mail out of a provider is not part of Towpath.

## Running the first slice

Requires Python 3.11 or newer. Everything runs on generated synthetic data, with no network access or credentials.

```sh
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest                        # 94 tests: acceptance checks, slice 1b, adapters, sync hardening
towpath fixtures generate /tmp/towpath-demo
cd /tmp/towpath-demo
towpath connect sync                    # index synthetic accounts and destination folders
towpath scan run                        # find attachments; request the bytes it needs
towpath connect fetch-requests          # fetch only those parts
towpath scan run                        # presence checks and delivery proposals
towpath proposals list
```

Proposals cannot be executed; there is no write path. See [first slice](docs/first-slice.md) for what it tests and what it taught, and `towpath --help` for every command.

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
| [File discovery](docs/file-discovery.md) | Optional search of folders, archives, and old mail backups through an existing tool (Recoll), with lineage, grants, recovery, and context packets |
| [File discovery providers](docs/evaluations/file-discovery-providers.md) and [native evaluation](docs/evaluations/file-discovery-native.md) | Interface evidence for Recoll and sist2, and their results on synthetic files |
| [First slice](docs/first-slice.md) | Smallest read-only synthetic email build and its checks |
| [Local quickstart](docs/setup/local-quickstart.md) | Exact steps to test against your own Gmail, Paperless, Immich, and a local model |
| [File discovery handoff](docs/setup/file-discovery-handoff.md) | Numbered steps for a local agent to validate file discovery on real archives |
| [Local agent handoff](docs/setup/local-agent-handoff.md) | A prompt for a coding agent on your machine to continue the verification |
| [Tooling review, October 2026](docs/research/2026-10-tooling-review.md) | Research on existing tools, licenses, Gmail access rules, and model servers, with sources |
| [Inbox Zero evaluation plan](docs/evaluations/inbox-zero-plan.md) | Checks to run on a dedicated test mailbox before relying on the integration |
| [Decisions](docs/decisions.md) | Accepted, withdrawn, and open decisions; facts to verify |
| [Roadmap](docs/roadmap.md) | Dependency map of milestones |
| [Publication](docs/publication.md) | What belongs in this public repository and what stays private |

## Public project boundary

The public repository holds portable application code, schemas, generic documentation, synthetic fixtures, and release assets. Accounts, mail, photos, credentials, hostnames, network layouts, and personal research stay in each user's private deployment. [Publication rules](docs/publication.md) describe the boundary.

This project is not affiliated with OpenAI, Google, or the authors of the tools it may integrate with. License: [MIT](LICENSE).
