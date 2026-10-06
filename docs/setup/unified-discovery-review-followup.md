# Cloud follow-up: local review of unified discovery

Status: completed on `claude/happy-gauss-ugprmu`. The local fixes were incorporated, and the three gates were fixed in `e19502b`, `ac32c0b` and `bb95895`. See [resolution](../evaluations/unified-local-review.md#resolution-on-the-implementation-branch).

Continue from PR [#4](https://github.com/endthestart/towpath/pull/4), original head `594c133`. Read [local findings](../evaluations/unified-local-review.md) and the original [foundation handoff](unified-discovery-cloud-handoff.md). The local fixes are on `codex/unified-local-review-fixes`; review and incorporate that branch before continuing, without losing your existing work. Keep the PR's target `codex/local-email-ui`. Nothing is authorized for main merge, publication, deployment or private-source access.

## Existing local fixes

`tests/test_unified_local_review.py` reproduces five corrected cases: unverified collection members, partial-evaluation baseline retention, absent cached mail parts, label ordering across run-ID width change, and selected attachment identity. Preserve these tests and the corrections in collections, requests, sources and the connector's label helper.

## Fix these three gates in separate tested commits

1. **Upgrade existing databases.** The new UI must work with stores created by `3b13228`. Current read-only readers fail on the missing new tables. Add an explicit idempotent upgrade procedure respecting store writer roles, and clear preflight/error handling. Test the actual previous schemas for source, queue and decisions, preserve all old content and approvals, and repeat the upgrade. The web process must not migrate the source store. Document exact local commands; no full mail rescan should be required.
2. **Avoid losing expanded attachment results.** `MailAdapter._provider` expands matched messages into parts and truncates `results[:limit]`, but its cursor has already advanced over the matched message. A single message with two matching parts and limit 1 loses the second part with no continuation. Design a continuation that preserves overflow and provider identity for Gmail and IMAP. Use synthetic protocol/client tests, including multiple attachments, multiple messages, page boundaries and typed filtering.
3. **Enforce current policy for cached search results.** The UI displays `requests.latest_search` verbatim after a grant is revoked. Reapply current root search grants, exclusions, configured source scope and availability before showing those stored results. Do this from local configuration/stores only; no provider access in the UI. Test revocation, changed exclusion patterns, disabled/removed sources and permitted results remaining visible. Report withheld/stale results honestly.

## Self-contained reproduction guidance

The public `tests/unified_env.py` helper `Uni(tmp_path, monkeypatch)` builds a synthetic Gmail/IMAP/files environment. Its IMAP server uses loopback only. Close it in fixture teardown.

- **Upgrade:** in a `Uni` environment, open `source` as `connect`, drop `search_runs`, then open the configured Django search view with query `canal`. The current result is `no such table: search_runs`. For the final test, construct stores from the base revision's exact schemas rather than merely dropping one table.
- **Pagination:** select the Gmail NEF part in `source.db`, insert a second named NEF part under the same message, then stub the connector's `search` to return that one native message ID with no provider continuation. Call `MailAdapter.search(Filters.parse('aqueduct extension:nef'), None, 1)`. It currently reports one result and no continuation. Check that paging the corrected result returns both parts exactly once. Mirror this with IMAP's real synthetic protocol path.
- **Cached grants:** `Filters.parse('canal source:files-fixture')`; queue it with `requests.request_search`, run `requests.run_searches`, and capture the archive-root references from `requests.latest_search`. Revoke `archive`'s search grant with `policy.revoke`. Open `/search/` for the same query. URL-decode the reference links in the returned HTML; the three revoked references currently remain in the provider-results section.

Run Ruff, fixture-domain checks, relative-link checks and the full suite before each commit. Add failing regressions before each correction, then commit only with passing checks. Return exact commits, CI results, updated local acceptance instructions, and any unresolved gates. Live TLS/server behavior, Gmail queries, Recoll/NAS coverage and private configuration remain the local operator's work. MCP, RAG, AI and the separate GHCR release blocker remain outside this follow-up.
