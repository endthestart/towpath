# Towpath

Towpath is an open-source, self-hosted workspace to discover your digital life, organize and reuse its contents, and build an evidence-linked timeline and portfolio. Email is the first working source. See the [product vision](docs/vision.md).

**Status: a web service and connector services, installed from container images ([install guide](docs/setup/install.md)).** Behind a single-account login, accounts are added in the browser: Gmail (read-only, with your own OAuth client), IMAP mail (Fastmail, iCloud, Yahoo, or Gmail with an app password), CardDAV/CalDAV contacts and calendars, Facebook and Apple Messages exports, and folders, archives and old mail backups searched through Recoll. Paperless-ngx and Immich are read for lookups, and Inbox Zero for statistics and rules. Mail management over IMAP proposes changes; a separate action runner carries out only what the owner asked for or approved, and runs each action type as a dry run until the owner turns it on ([mail management](docs/mail-management.md)). The life stream finds events in allowed mail, gathers them into memories and answers questions with cited sources ([life stream](docs/life-stream.md)). Model features use explicitly configured endpoints under policy. The same UI also runs locally over a saved index ([local UI](docs/setup/local-ui.md)). Acceptance tests here use synthetic data and stubs; live deployment checks stay private. Images are built, tested and published by GitHub Actions under [release gates](docs/setup/containers.md).

## What it will do

- **Discovery and reuse:** search connected accounts, files, exports and existing inventories; curate reference collections and source-linked packets for other applications or agents. See the [project rediscovery pilot](docs/specs/project-rediscovery-pilot.md).
- **Mail management:** unsubscribing, important mail still waiting for your reply, draft replies, categories, and smart rules, built into Towpath over IMAP ([ADR 0002](docs/adrs/0002-towpath-native-mail-management.md)), with Inbox Zero as the interim tool for Gmail. Towpath adds routing attachments to your document system or photo library, through the separately permissioned action runner after you approve.
- **Life stream:** find people, events, places, photos, and documents across connected sources and turn them into reviewable claims with citations, uncertain dates, and visible contradictions. Ask questions and get cited answers. In the long term, curate a story to share with family. It starts from mail but works with any source.

## How it is built

Towpath is the front end and coordinator. It connects to tools people already run (for example a document system or photo library) or bundles open-source tools in its Docker Compose file, and writes code only where nothing suitable exists. Everything Towpath requires is open source; optional components may be source available with use restrictions if they are free for personal self-hosting ([license policy](docs/integrations.md)). See [integrate first](docs/architecture.md#integrate-first).

Model features use explicitly configured OpenAI-compatible endpoints: base URL, credential, model, and destination. Poundlock is one optional endpoint; a bundled local model server or any compatible API works the same way. Personal data is not sent to any endpoint outside the machine unless the owner grants it, and items can be kept local-only or excluded from models entirely. See [model providers](docs/model-providers.md).

Discovery and curation come first. Durable copies, source independence and physical organization are later phases with separately scoped operational workflows ([ADR 0001](docs/adrs/0001-reference-first-digital-life.md)).

## Running the first slice

Requires Python 3.11 or newer and [uv](https://docs.astral.sh/uv/). Dependencies are pinned in `uv.lock`, which local development, CI and the container images all install from. Everything runs on generated synthetic data, with no network access or credentials.

```sh
uv sync --locked --extra dev
source .venv/bin/activate
python -m pytest                        # acceptance checks for mail, models, discovery, releases, and UI
towpath fixtures generate /tmp/towpath-demo
cd /tmp/towpath-demo
towpath connect sync                    # index synthetic accounts and destination folders
towpath scan run                        # find attachments; request the bytes it needs
towpath connect fetch-requests          # fetch only those parts
towpath scan run                        # presence checks and delivery proposals
towpath proposals list
```

These delivery proposals cannot be executed; there is no delivery path yet. See [first slice](docs/first-slice.md) for what it tests and what it taught, and `towpath --help` for every command.

## Install on a home server

One data folder, a Compose file pasted into Arcane (or any Compose host), and the rest in the browser: see the [install guide](docs/setup/install.md).

## Local email UI

Install the optional interface, then point it at the folder containing your existing `source.db`:

```sh
uv sync --locked --extra web
towpath web serve --store-dir /path/to/private/state
```

Open `http://127.0.0.1:8790/` and create the instance's one account with the setup code the server printed. The overview, email search, Inbox/Sent filters, and attachment references read the index only. Message bodies and file contents are not displayed or searched. For a credentials-free demo and the local preview's limits, see [local UI setup](docs/setup/local-ui.md).

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
| [Container images and releases](docs/setup/containers.md) | Images built and tested in GitHub Actions, published to GHCR, pulled by digest; pull, deploy, verify, roll back |
| [File discovery handoff](docs/setup/file-discovery-handoff.md) | Numbered steps for a local agent to validate file discovery on real archives |
| [Unified discovery](docs/unified-discovery.md) and [local acceptance](docs/setup/unified-discovery-acceptance.md) | One source-aware search and evidence surface over Gmail, IMAP and file sources |
| [Local agent handoff](docs/setup/local-agent-handoff.md) | A prompt for a coding agent on your machine to continue the verification |
| [Tooling review, October 2026](docs/research/2026-10-tooling-review.md) | Research on existing tools, licenses, Gmail access rules, and model servers, with sources |
| [Inbox Zero evaluation plan](docs/evaluations/inbox-zero-plan.md) | Checks to run on a dedicated test mailbox before relying on the integration |
| [Decisions](docs/decisions.md) | Accepted, withdrawn, and open decisions; facts to verify |
| [Roadmap](docs/roadmap.md) | Dependency map of milestones |
| [Publication](docs/publication.md) | What belongs in this public repository and what stays private |

## Public project boundary

The public repository holds portable application code, schemas, generic documentation, synthetic fixtures, and release assets. Accounts, mail, photos, credentials, hostnames, network layouts, and personal research stay in each user's private deployment. [Publication rules](docs/publication.md) describe the boundary.

This project is not affiliated with OpenAI, Google, or the authors of the tools it may integrate with. License: [MIT](LICENSE).
