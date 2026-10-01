# Towpath

Towpath is an open-source project for managing your digital life, starting with email, and using that information to create an evidence-linked life summary. It will offer user-controlled tools for handling daily information alongside a reviewable record of people, events, places, and memories.

**Status: architecture and roadmap.** This repository does not yet contain a runnable application, mailbox connector, or archive. The first release is a public design so implementation can happen in the open without publishing anyone's personal data or infrastructure.

The work has three distinct concerns:

1. **Mail management:** classify, find missed messages, propose organization and unsubscribe actions, and let a person review changes to a live mailbox.
2. **Life summary:** connect mail, messages, calendars, photos, documents, and first-person recollections into claims about people, events, places, and time. Answers and stories cite evidence and carry uncertainty.
3. **Personal mail evacuation:** moving a particular person's historical Gmail into a local living archive and eventually removing provider copies. This is an independent migration and preservation project. Towpath may read an archive through a documented adapter, but its mail management and life summary do not require that migration.

Read the [architecture](docs/architecture.md) for component boundaries and the [mail boundary](docs/mail-boundaries.md) for exactly what can change a mailbox. The [roadmap](docs/roadmap.md) marks what exists and what is proposed.

## Model providers

Towpath's first inference contract will use a configurable OpenAI-compatible Chat Completions endpoint: base URL, API key, and model ID. A local server or gateway such as Poundlock can fill that role; Poundlock is optional. OpenAI's API can also be configured explicitly. No model provider or network destination is selected automatically. Later capabilities, such as embeddings and transcription, are negotiated independently. See [provider design](docs/model-providers.md).

## Public project boundary

The public repository holds portable application code, schemas, generic documentation, synthetic fixtures, and release assets. Accounts, mail, photos, credentials, hostnames, network layouts, machine benchmarks, deployment secrets, and personal research stay in each user's private deployment. [Publication rules](docs/publication.md) describe the boundary.

This project is not affiliated with OpenAI or the authors of the tools it may integrate with. License: [MIT](LICENSE).
