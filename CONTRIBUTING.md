# Contributing

Towpath is in design stage. Discuss architecture in public issues and use synthetic examples. Do not post real messages, contact details, credentials, private endpoint URLs, or infrastructure inventories in issues, pull requests, or test fixtures.

The [architecture](docs/architecture.md) and [services](docs/components.md) define authority boundaries; integrations must be FOSS and follow the [evaluation checklist](docs/integrations.md#evaluation-checklist); open questions are tracked in [decisions](docs/decisions.md). Contributions to provider support should state the exact API subset tested. Contributions to evidence handling should preserve source references, date precision, and review history. Mailbox actions require a separate permission path and a reviewable execution record, and must stay within the scope of [decision D1](docs/decisions.md#d1-mailbox-and-destination-execution).

The repository uses the [MIT license](LICENSE). The first implementation milestone will add build and test instructions once a runnable application exists.
