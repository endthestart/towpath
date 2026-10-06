# Unified discovery: local acceptance

Status: handoff for the local operator. The cloud branch was built from GitHub-visible code and synthetic fixtures only. Each increment below lists its automated tests, exact local steps, and what still needs live verification. Private configuration (accounts, folders, credentials) stays outside the repository; examples use reserved names.

Prerequisite for every increment:

```sh
uv sync --locked --extra dev && source .venv/bin/activate
python -m pytest -q -rs
ruff check src tests packaging
towpath fixtures check-domains src tests docs examples deploy packaging README.md CONTRIBUTING.md
```

## Before anything else: upgrade existing stores

Stores created by an earlier version, including `codex/local-email-ui` at `3b13228`, lack tables that this branch reads: `source.search_runs`, `queue.search_requests`, and `decisions.source_grants`, `collections` and `collection_items`. Readers open stores read-only and never migrate them. Until the upgrade runs:

- `towpath web serve` refuses to start;
- unified UI pages answer with a 503 page that names the command;
- `towpath search`, `collections` and `claims` commands stop with `store-upgrade-needed`.

The Email pages keep working, because they need nothing new.

```sh
# stop the UI and any running towpath command; keep a copy of the store folder first
cp -a /path/to/private/state /path/to/private/state-backup-$(date +%Y%m%d)
towpath stores status  --store-dir /path/to/private/state   # read-only; exit 1 lists what is missing
towpath stores upgrade --store-dir /path/to/private/state   # adds the missing tables and columns
towpath stores status  --store-dir /path/to/private/state   # exit 0: every store current
```

- **Writer roles.** Each store is opened only by its own writer role: source by connect, queue and decisions by web, files by connect.
- **Data safety.** Only `CREATE TABLE IF NOT EXISTS` and `ADD COLUMN` run. Every message, part, run, cache entry, request, grant, setting and decision stays as it was.
- **Repeatable.** Running the upgrade again changes nothing.
- **Missing stores.** Stores that do not exist are not created.
- **No rescan.** No mail resync is needed.

Automated: `tests/test_store_upgrade.py`. It builds source, queue and decisions stores from the exact DDL of `3b13228` (`tests/legacy_stores_3b13228.py`), with invented content and approvals, and checks:

- status and upgrade results;
- content preserved;
- a repeated upgrade is a no-op;
- each store is opened only by its writer role;
- the UI refuses to start and answers 503 without touching `source.db`;
- the UI works after the upgrade;
- the CLI preflight.

Live (local operator): run the four commands above against a copy of the private store first. Compare `towpath connect status <source>` before and after (indexed counts unchanged). Then upgrade the real folder.

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
- `tests/test_gmail_client.py::test_provider_search_uses_q_under_the_same_scope_client_and_budget`;
- `tests/test_provider_pagination.py`: provider pages that expand into several parts, for Gmail (stubbed provider over the real index) and IMAP (real protocol path). It covers limits 1 to 5, multiple attachments and messages, typed filtering, and a changed provider answer reported as `stale`.

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
5. `towpath search query "walk extension:nef" --limit 1`, then repeat with `--cursor <next_cursor>` until it is `null`. Expect each matching NEF part exactly once (Gmail `DSC_0042.NEF`, IMAP `DSC_0044.NEF`). A page that ends partway through a message's parts continues from that message.
6. Stop the stub (`kill %1`) and repeat step 3. Expect `imap-fixture` with status `unavailable` and error `server-stop`, while the other sources still answer.
7. To see the stub's own check, run it in the foreground in a second terminal instead of with `&`. When stopped with Ctrl-C, it prints `violations: []` if no write or non-PEEK read was attempted.

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
8. Request one attachment through a scan or a queued content request, then run `towpath connect fetch-requests`. Confirm the bytes match the original and the message is still unread.

**Gmail**

1. `towpath search query "<word in a known message body>" --source <gmail-id>`. Expect `provider-search` results with an `estimate`.
2. Pick a query whose messages have several matching attachments, for example `"<word> extension:pdf"`. Page with `--limit 1` and confirm every attachment appears exactly once.
3. `towpath connect status <gmail-id>`. Confirm the budget shows the extra `users.messages.list` calls (5 units each), and that the scope is still `gmail.readonly`, with no re-authorization asked.

**Files**

1. `towpath search query "extension:nef" --local` against a real Recoll provider. NEF files appear only if Recoll catalogs them. Otherwise, point a `manifest` provider at an existing inventory of the photo folders and import it.
2. Confirm `incomplete_because` lists every ungranted root and every incomplete import.

### Still synthetic in this increment

- IMAP behavior against a real server: TLS, SEARCH semantics, limits, folder names.
- Gmail `q` against the live API: only a fake of Google's discovery client is exercised.
- Manifest import of a real inventory: the format is new, so an existing inventory may need a small conversion script, written locally.

## Increment 3: UI and collections

Built:

- Search all, with per-source status, depth, notes and incompleteness;
- reference inspection;
- selected-content requests, with escaped plain-text display;
- queued provider searches;
- durable set and query collections, with evaluation and accepted baselines;
- CSRF-protected POST forms.

See [local UI](local-ui.md#unified-search-and-collections-development-branch) and [unified discovery](../unified-discovery.md#collections).

Automated: `tests/test_unified_web.py` and `tests/test_cached_search_policy.py`. The second checks that stored provider results respect revoked grants, new exclusions, removed or disabled sources, absent messages and mailbox scope, that permitted results stay visible, and that no configuration means stored file results are withheld. The first covers the UI never contacting a source, CSRF, escaping, the queue, collections surviving a deleted and re-imported `files.db`, query baselines, and decisions-only writes. `tests/test_web.py` still passes for the original pages.

### A. Synthetic (continue from increment 2's `uni` folder, with the stub running)

1. `towpath web serve --store-dir state --config towpath.toml`. Open `http://127.0.0.1:8790/search/?q=extension:nef`. Expect four source groups, each tagged `catalog`, and the notice "Not a complete answer" naming the incomplete inventory.
2. Search `fundraiser`. Expect no IMAP result, and a note that keyword text matched metadata only. Choose **Search inside messages**, then run `towpath search run-requests` in another terminal; the waiting page refreshes itself. Expect "Canal boat club minutes" under *Inside messages*, with when it ran, and the note that Towpath has not verified a passage under *About these results*.
   Then, for stored results under current policy:

   ```sh
   # queue "canal source:files-fixture" from the page, then:
   towpath search run-requests
   towpath files revoke archive search
   ```

   Reload the page. The three archive results are withheld, with a notice "3 stored results withheld … no search grant on root archive now", and the shared-root result stays. Re-grant with `towpath files grant archive search` afterwards.
3. Open the IMAP "Lock keeper's log" result, then its part 1. Choose **Request this part**, run `towpath connect fetch-requests`, and reload. The text shows the literal `<script>` characters, escaped, under the untrusted-text notice.
4. On **Collections**, create a reference set. Add two NEF references from their reference pages, then open the set. Expect `unchanged` for the inventory items. Delete `state/files.db`, run `towpath files import --provider inventory`, and reload. Expect the same references, still resolving.
5. Save the `extension:nef` search as a query collection, open it and accept the current results. In `files/manifests/photos.jsonl`, change `"complete": false` to `true`, delete the `escape.NEF` line and one NEF line, and change another NEF line's `mtime`. Re-import and reload. Expect `removed`, `changed`, `unchanged`, and `unverified` for the Gmail part.

### B. Live (local operator)

1. Start the UI with the private store and config. Confirm the source list shows every configured source, with honest coverage and ungranted roots.
2. Search for an owner-selected example across mail and files. Queue a provider search and run `towpath search run-requests`. Confirm the Gmail and IMAP provider results and their stored timestamp.
3. Request one plain-text part and run `towpath connect fetch-requests`. Confirm the text appears, and that the message's read state in the mail client is unchanged for IMAP.
4. Build an owner-chosen collection, such as NEF photographs or project documents. Re-run an index import and confirm the collection still resolves.

Still synthetic: the browser checks used Chromium on the generated environment only. No live account was used.

## Increment 4: agent interface and evidence structures

Built:

- the `towpath.context/1` packet (file items reuse the files packet unchanged);
- consumer grants for mail sources;
- citation verification;
- collections and claims commands;
- typed-date and claim validation, round trips and timeline ordering.

See [unified discovery](../unified-discovery.md#context-packets-towpathcontext1).

Automated:

- `tests/test_unified_context.py`: grants, exclusions, stale and absent references, budgets, untrusted labelling, collections, local mode, the unchanged files packet and the CLI;
- `tests/test_claims.py`: precision rules, process-date refusal, the draft example shape, contradictions, round trips and the CLI.

### A. Synthetic (continue from the `uni` folder, with the stub running)

1. `towpath search context --purpose agent-context --query canal`. Expect `items: []`, and every reference omitted as `denied`.
2. Grant purposes:

   ```sh
   for r in archive shared; do towpath files grant $r agent-context; done
   towpath files grant archive excerpt
   towpath search grant gmail-fixture agent-context
   towpath search grant imap-fixture agent-context
   ```

   Repeat step 1. Expect Gmail, IMAP and file items. Archive items carry excerpts with `towpath.citation/0` citations. Shared and mail items give `excerpt_omitted_reason`. Mail items found by provider search say no passage was verified.
3. Run `towpath search grant imap-fixture excerpt`. Request and fetch part 1 of the lock keeper's log (from the UI, or from increment 3's step 3). Then run:

   ```sh
   towpath search context --purpose agent-context --ref "imap-fixture:Archive/2008;UIDVALIDITY=1700000002;UID=2" --excerpt-bytes 40
   ```

   Expect the `<script>` text, cut at 40 bytes and labelled untrusted. Pass its citation to `towpath search cite '<json>'` and expect `current`.
4. Append bytes to `files/roots/shared/copy-of-paper.docx` and build a packet with that occurrence's reference. Expect it omitted as `stale`.
5. Run `towpath collections create NEF --query extension:nef`, then `towpath collections evaluate NEF --accept`, then `towpath collections evaluate NEF --local`. Expect five `added`, then `unchanged`, with Gmail `unverified`.
6. Run `towpath claims validate $REPO/examples/claims.example.json` (valid) and `towpath claims timeline $REPO/examples/claims.example.json` (one contradiction listed, four entries).

### B. Live (local operator)

1. Grant one owner-chosen mail source and one root the `agent-context` purpose. Build a packet from an owner-selected query or collection. Confirm that versions, citations and bounded excerpts appear only where granted, and that a revoked grant turns items into `denied` omissions.
2. Mark one item `towpath item set <item_id> --model-use excluded` and confirm it is omitted.
3. Verify one file citation and one mail citation with `towpath search cite`. Then change the source file (or re-fetch a different part) and confirm the citation is `stale`.
4. Write two or three real timeline claims for an owner-chosen event outside the repository, and validate them. Recovery or export dates must be rejected as event dates.

### Still synthetic or not built in this foundation

- No MCP transport, RAG or model enrichment. These were stretch goals and none was attempted.
- Claims are validated, not stored, and nothing extracts them automatically.
- Mail excerpts depend on parts the owner fetched first. No body text index is built.
