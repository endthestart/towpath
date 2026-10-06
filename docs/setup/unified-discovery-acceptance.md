# Unified discovery: local acceptance

Status: handoff for the local operator. The cloud branch was built from GitHub-visible code and synthetic fixtures only. Each increment below lists its automated tests, exact local steps, and what still needs live verification. Private configuration (accounts, folders, credentials) stays outside the repository; examples use reserved names.

Prerequisite for every increment:

```sh
pip install -e ".[dev]"
python -m pytest -q -rs
ruff check src tests packaging
towpath fixtures check-domains src tests docs examples deploy packaging README.md CONTRIBUTING.md
```

## Increment 1: contracts

Built: source status, result envelope, typed filters, per-source paging and errors, coverage states, typed dates, citations, legacy wrapping, and a synthetic federation demo. See [unified discovery](../unified-discovery.md).

Automated: `tests/test_unified_contracts.py`.

Local steps (synthetic, no private data needed):

1. `towpath search demo "aqueduct"`. Expect three sources: `gmail-fixture` and `imap-fixture` at `provider-search` depth, and `files-fixture` at `content-index` depth with status `partial`. Expect `complete: false`, with reasons that name the partial files inventory and provider-search coverage.
2. `towpath search demo "extension:nef"`. Expect two `.NEF`/`.nef` file references with coverage `unsupported` and no excerpt, plus one Gmail attachment part (`#part=1`). Every page has depth `catalog`, and `complete` is false because the files inventory is partial.
3. `towpath search demo "aqueduct" --imap-mode unavailable`. Expect `imap-fixture` with status `unavailable` and an `unavailable` error, while the Gmail and file results remain.
4. `towpath search demo "extension:nef" --files-mode ok --limit 1`, then repeat with `--cursor <next_cursor>`. Expect the second page to ask only `files-fixture`.

Still synthetic: everything in this increment. Real sources are attached in increment 2.
