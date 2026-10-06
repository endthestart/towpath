# Unified foundation: local review

Reviewed PR [#4](https://github.com/endthestart/towpath/pull/4), head `594c133e5a72bcc4d7a4cad370428faa0a0f3be6`, against base `3b13228`. All review data was synthetic. No live source, private index, mailbox or NAS was read or changed. No merge, publication or deployment was performed.

## Validation

The submitted revision passed local Ruff and the fixture-domain scan. Full local tests: **362 passed, 5 skipped**, exit 0, measured duration 109.95 seconds. Skips were Recoll/native binding (four) and sist2 (one); this accounts for the cloud's 366 passed/1 skipped. Local testing used Python 3.14 and IMAPClient 4.1.0 in a disposable dependency folder, without changing the running application environment.

Five additional regression cases failed before the local fixes and passed afterward:

- Re-accepting a query collection lost a resolving Gmail member without a version token: five baseline entries became four. Resolving `unverified` members are now included.
- Accepting a partial evaluation after revoking a root's search grant discarded unavailable baseline members. Their accepted references and versions are now retained until a complete answer can establish removal.
- A fetched part of a subsequently absent message was still reported as `fetched` and served text. Absence now takes precedence; the cached bytes are retained but not presented as available source content.
- Label observations were ordered by string run identifiers. At the `run_9999`/`run_10000` boundary, the older labels won. The connector helper and unified adapter now use the numeric run sequence.
- Describing an attachment reference returned its parent message's result identity and metadata. The selected part now retains its own reference, kind, name and media type.

Regressions: `tests/test_unified_local_review.py`. These fixes do not resolve the remaining gates below.

After the fixes, the full suite passed: **367 passed, 5 skipped**, exit 0, measured duration 111.88 seconds. Ruff, fixture-domain checks, whitespace checks and all 286 relative file links passed. PR #4's ten test/image checks passed; its two publication jobs were skipped as designed. A separate loopback UI preview over generated sources returned five NEF references across Gmail, IMAP and inventory sources, including one archive member, with incomplete coverage explicitly shown. This was a synthetic interface check, not real-source acceptance.

## Remaining gates before real-data adoption

| Gate | Synthetic evidence | Required correction |
| --- | --- | --- |
| Existing-store upgrade | Removing only the new `search_runs` table from a synthetic source database reproduces an existing index: GET `/search/?q=canal` raises `sqlite3.OperationalError: no such table: search_runs`. Readers open existing databases read-only and do not migrate them. Previous queue and decisions stores also lack newly added tables. | Provide an explicit, idempotent upgrade path under each store's permitted writer role, or a clearly handled compatible read path. Show an actionable message rather than HTTP 500. Test prior schemas and preserve existing data/role separation. Do not rely on re-syncing a whole mailbox. |
| Attachment search pagination | One provider-matched Gmail message with two NEF parts and per-source limit 1 returns one part, `next_cursor: null`, `more_may_exist: false`. `_provider` expands message rows into attachment results, then slices them, losing overflow after consuming the provider cursor. | Preserve every expanded result across continuation pages for Gmail and IMAP. Test multiple attachments in one message, page boundaries, mixed filters and provider cursors. |
| Cached provider results and current grants | Queue/run `canal source:files-fixture` in `Uni`; revoke `archive`'s `search` grant; GET the same search page. The fresh catalog obeys revocation, but all three old archive reference links remain in the stored-provider section. | Reapply current scope/grants/exclusions/source availability before exposing stored results. Filtering must remain store-only in the UI, with no credential, connector or provider subprocess. Preserve useful permitted results and show why other stored results were withheld. |

See the [cloud follow-up](../setup/unified-discovery-review-followup.md). A synthetic preview may be used for evaluation while these remain open; neither passing synthetic tests nor that preview qualifies real accounts or NAS indexing.

## Resolution on the implementation branch

The local fixes above were incorporated unchanged, as a fast-forward to `96ca401`. The three gates were then fixed in separate commits on `claude/happy-gauss-ugprmu`. Each commit added failing synthetic regressions first, then passed Ruff, the fixture-domain scan, the relative-link check and the full suite.

| Gate | Commit | Fix | Regressions |
| --- | --- | --- | --- |
| Existing-store upgrade | `e19502b` | `towpath stores status\|upgrade`. Each existing store is opened only by its writer role, using only `CREATE TABLE IF NOT EXISTS` and `ADD COLUMN`; the upgrade is idempotent and creates no missing stores. The UI refuses to start, and unified pages answer 503 with the command, until the upgrade has run. The UI never migrates `source.db`. CLI preflight: `store-upgrade-needed` | `tests/test_store_upgrade.py`, using the exact 3b13228 DDL in `tests/legacy_stores_3b13228.py` |
| Attachment pagination | `ac32c0b` | The continuation `{"page": provider token, "after": last ref}` resumes inside an expanded provider page. If the provider answer changed, the source reports `stale` | `tests/test_provider_pagination.py` (Gmail stubbed provider over the real index; IMAP through the real synthetic protocol) |
| Cached results and current policy | `bb95895` | `requests.apply_current_policy` re-checks grants, exclusions, configured sources and mailbox scope, and catalog or index availability, from local stores only. Withheld results are counted per source with reasons | `tests/test_cached_search_policy.py` |

After these commits, the cloud suite reports 393 passed, 1 skipped (sist2). Real accounts, the NAS and private configuration remain the local operator's acceptance; see [the acceptance guide](../setup/unified-discovery-acceptance.md).
