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

## Increment 2: provider adapters

Built:

- the read-only IMAP connector;
- Gmail provider search (`q`);
- a `manifest` files provider for inventory references;
- unified adapters over the real stores and providers;
- `towpath search sources|query|describe`;
- a synthetic environment generator.

See [unified discovery](../unified-discovery.md#source-adapters), [IMAP client evaluation](../evaluations/imap-client.md) and [manifest adapter](../file-discovery.md#manifest-adapter).

Automated:

- `tests/test_imap.py`: the synthetic IMAP server, transcripts and identity cases;
- `tests/test_unified_sources.py`: Gmail, IMAP, files and manifest adapters together;
- `tests/test_gmail_client.py::test_provider_search_uses_q_under_the_same_scope_client_and_budget`.

### A. Synthetic end to end (no private data)

Run these from a scratch folder outside the repository. `$REPO` is your checkout.

```sh
export TOWPATH_FIXTURE_IMAP_PASSWORD=synthetic-only
python $REPO/tests/imap_stub.py 11430 &          # synthetic read-only IMAP server on loopback
towpath fixtures unified ./uni --imap-port 11430
cd uni
towpath connect sync gmail-fixture imap-fixture
for r in archive shared photos; do towpath files grant $r search; done
towpath files import --provider fixture
towpath files import --provider inventory
```

1. `towpath search sources --local`. Expect four sources. Gmail and IMAP are `ready`, with depths `["catalog"]` and `provider-search: disabled`. `files-inventory` has `inventory_complete: false`.
2. `towpath search query "extension:nef" --local`. Expect one Gmail part (`DSC_0042.NEF`), one IMAP part (`DSC_0044.NEF`), and three inventory references, including `DCIM/DSC_0001.NEF` inside `2003/camera-backup.zip`. Expect `complete: false` because the manifest declares an incomplete scope.
3. `towpath search query "canal"`. Expect Gmail and IMAP at `provider-search` depth, `files-fixture` at `content-index` depth, and `files-inventory` at `catalog` depth. The results include the Gmail newsletter, both IMAP messages that mention the canal, and the paper occurrences.
4. `towpath search query "fundraiser"` finds the IMAP minutes, a body-only match. With `--local`, it finds nothing at IMAP, and `incomplete_because` says the match was metadata only.
5. Stop the stub (`kill %1`) and repeat step 3. Expect `imap-fixture` with status `unavailable` and error `server-stop`, while the other sources still answer.
6. To see the stub's own check, run it in the foreground in a second terminal instead of with `&`. When stopped with Ctrl-C, it prints `violations: []` if no write or non-PEEK read was attempted.

### B. Live verification (local operator, private configuration)

Prerequisites: `pip install -e ".[imap]"`, and a private config outside the repository.

**IMAP**

1. Add the IMAP source (see [unified discovery](../unified-discovery.md#imap)), with the password as a credential reference. Run `towpath config check` and confirm the password is reported `present` without being printed.
2. `towpath connect probe <imap-id>`. Expect `ok: true` and the mailbox count. A configured mailbox missing from LIST makes `missing_mailboxes` greater than zero.
3. Note the unread count of one mailbox in your normal mail client.
4. Run `towpath connect sync <imap-id> --max-items 50`, then `towpath connect sync <imap-id>`. Expect `capped` first and then `complete`, with no duplicates (`towpath connect status <imap-id>`).
5. Confirm the unread count from step 3 is unchanged. If the server keeps a protocol log, confirm it shows only EXAMINE, UID SEARCH, UID FETCH (BODY.PEEK) and LOGOUT.
6. Move one message to another folder in your mail client. Run sync again. Expect the old identity to be marked absent and the new identity to be indexed.
7. `towpath search query "<a word known to be only in a message body>" --source <imap-id>`. Expect the message at `provider-search` depth. Record whether your server's SEARCH matches substrings or whole words, and whether it searches attachments.
8. Request one attachment through a scan or a queued content request, then run `towpath connect fetch`. Confirm the bytes match the original and the message is still unread.

**Gmail**

1. `towpath search query "<word in a known message body>" --source <gmail-id>`. Expect `provider-search` results with an `estimate`.
2. `towpath connect status <gmail-id>`. Confirm the budget shows the extra `users.messages.list` calls (5 units each), and that the scope is still `gmail.readonly`, with no re-authorization asked.

**Files**

1. `towpath search query "extension:nef" --local` against a real Recoll provider. NEF files appear only if Recoll catalogs them. Otherwise, point a `manifest` provider at an existing inventory of the photo folders and import it.
2. Confirm `incomplete_because` lists every ungranted root and every incomplete import.

### Still synthetic in this increment

- IMAP behavior against a real server: TLS, SEARCH semantics, limits, folder names.
- Gmail `q` against the live API: only a fake of Google's discovery client is exercised.
- Manifest import of a real inventory: the format is new, so an existing inventory may need a small conversion script, written locally.
