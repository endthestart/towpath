# Gmail sync hardening: implementation handoff

Status: **implemented and tested on synthetic data only** (2026-10-02). Live full-mailbox, incremental, and D16 validation remain pending with the local agent; see [local validation](#local-validation). The specification below is unchanged from the owner's handoff.

## Goal and boundaries

The owner prefers a slow background sync with substantial quota headroom. Speed
is secondary to predictable load, recoverable progress, and understandable status.
Implement this on `claude/happy-gauss-ugprmu`, using synthetic tests only. Local
validation and deployment remain with the owner's local agent.

Keep Gmail access exactly `gmail.readonly`. Do not add mailbox writes, models,
attachment downloads, Gmail migration, or deployment to this task. Credentials,
mail, database contents, project identifiers, and private infrastructure stay
outside this public repository.

## Verified findings

- A local structure-only sample returned no body data and no truncation. This is
  sample evidence, not a guarantee about all MIME structures.
- An unpaced capped sync exhausted its SDK retries and raised HTTP 403 with
  reason `rateLimitExceeded` and metric `Total Query Cost`, limit
  `Units per minute per user`.
- Progress was committed per message. A slower, test-only resume reached the
  intended cap without losing prior progress or downloading content.
- An unexpected exception leaves the run unfinished and without a coverage
  summary, although the full-sync checkpoint survives.
- Full-sync resume restarts listing at the beginning and skips already-seen IDs.
  That preserves progress but repeatedly lists old pages as the index grows.
- The local label fix applies top-level history `labelIds` as additions/removals.
  Google history messages typically contain only `id` and `threadId`; assuming
  `message.labelIds` caused a reproducible exception. Fixtures now use the
  documented response shape. Preserve that fix.

## Published limits, checked 2026-10-02

[Google's quota reference](https://developers.google.com/workspace/gmail/api/reference/quota)
distinguishes quotas for new projects from grandfathered projects and warns that
individual project quotas can differ. These published values do not establish
the actual limits of any deployment; confirm those in Cloud Console before a
full run.

| Limit or operation | Published value for new projects |
| --- | ---: |
| Per user per project per minute | 6,000 quota units |
| Per project per minute | 1,200,000 quota units |
| Daily project billing threshold | 80,000,000 quota units |
| `users.getProfile` | 1 unit |
| `users.messages.list` | 5 units |
| `users.messages.get` | 20 units |
| `users.messages.attachments.get` | 20 units |
| `users.history.list` | 2 units |

The daily figure is a published billing threshold, not permission to incur
charges. Billing announcements and effective dates must be checked separately
before relying on pricing. OAuth refresh and token-info calls are outside the
Gmail method table; handle their errors without aggressive polling.

Additional constraints:

- Message listing defaults to 100 IDs and allows at most 500. History listing
  also defaults to 100 records and allows at most 500.
- Gmail history cursors are usually valid for at least a week, but Google says
  they can expire after only hours. An expired history cursor requires a full
  rescan; a capped run must never falsely claim it completed the mailbox.
- The consent application remains in Testing during local setup. Its refresh
  token can expire after seven days; a clean reauthorization path is necessary.

Sources: [message listing](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/list),
[history listing](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.history/list),
[sync guidance](https://developers.google.com/workspace/gmail/api/guides/sync),
[OAuth token expiration](https://developers.google.com/identity/protocols/oauth2#expiration).

## Required behavior

1. Add validated per-deployment Gmail pacing settings, with conservative defaults:
   at least one second between request attempts, at most 1,200 quota units per
   minute, and a local daily project budget of 1,800,000 units. These are Towpath
   budgets, not claims about Google limits. Allow lower values when the actual
   project quotas demand them. At 20 units per message read, this is roughly one
   message per second before other requests and retries, with substantial
   headroom against the published per-user limit.
2. Account for every attempt using the method's quota cost, including retries,
   listing, verification, probes, history, and on-demand attachment reads. Route
   SDK retries through the same limiter or disable nested SDK retries. Avoid an
   initial burst. Defaults must apply to the ordinary CLI, not just a test wrapper.
3. Prevent simultaneous Towpath commands from multiplying the budget for the
   same project/account. A simple exclusive sync lock plus a shared persistent
   quota budget is preferable to adding another service. Restarting or changing
   commands must not reset the daily budget or allow an immediate burst.
4. Honor `Retry-After` when supplied. Use bounded exponential backoff with jitter
   for quota/transient errors, with cancellation during waits. Distinguish quota
   stops, permission/authentication failures, malformed requests, missing
   messages, and server failures. Exhausted retries should produce a clear
   resumable stop and nonzero exit, not a traceback containing request URLs.
5. Persist run termination and coverage for quota stops, errors, and Ctrl-C,
   including processed counts and a sanitized reason. Preserve committed items
   and the original full-sync history checkpoint. Never advance a cursor past
   unprocessed events or mark an incomplete run complete.
6. Improve full-sync resume so it does not list every previous page repeatedly.
   Handle invalid page tokens and mailbox changes without losing coverage.
   Preserve seen-ID reconciliation and do not treat a partial scan as evidence
   that messages are absent. Document the chosen correctness tradeoff.
7. Show bounded progress and waiting status without subjects, addresses, message
   IDs, authorization URLs, tokens, or contents. Report rate estimates as
   measurements with uncertainty; do not promise API ordering or completion time.

## Acceptance and delivery

- Use fake clocks and realistic fake Google responses. Test method-cost
  accounting, no startup burst, shared budgets/locks, daily stops, all retry
  attempts, cancellation while waiting, and distinct HTTP error reasons.
- Test a capped full sync, quota stop, Ctrl-C, resumed completion, expired page
  and history tokens, message disappearance, and incremental changes without
  `message.labelIds`. Ensure no duplicates, skipped events, false absence, or
  false complete state. Verify that errors close stores and record coverage.
- Test that no request mask includes body data, no automatic content fetch is
  introduced, and scopes remain exactly read-only.
- Update the example configuration, CLI help, quickstart, and first-slice status.
  Keep live full completion, incremental behavior, and D16 pending until the
  local agent verifies them. Do not turn synthetic tests into live claims.
- Run the full test suite, lint, fixture-domain scan, and internal-link checks.
  Supply commits and a short handoff explaining changed settings and local checks.

Before implementation, confirm that the owner's local preflight and label fixes
are present in the branch. If they are not yet published, ask for those commits
or the patch rather than independently changing the same code.

## Implementation

Every item below is tested on synthetic data with a fake clock and Google-shaped fake responses (`tests/test_sync_hardening.py`). None of it has run against Gmail.

| Requirement | Where | How |
| --- | --- | --- |
| 1. Pacing settings | `src/towpath/quota.py` (`PacingSettings`), `[gmail_pacing]` in config | Defaults are 1 s, 1,200 units per minute, and 1,800,000 units per day. They are also the maximums: config may only lower them. Values are validated, unknown keys are rejected, and `budget_id` names one budget per Cloud project |
| 2. Every attempt accounted | `QuotaLimiter.acquire`, `GoogleGmailClient._execute` | Each attempt books its method's published cost before sending. This covers retries, listing, history, profile probes, `verify-structure`, and attachment reads. The API client's own retries are disabled (`num_retries=0`). `build_connector` always attaches the limiter, so the ordinary CLI is paced |
| 3. Shared, persistent budget | `quota.db` `attempts` table; `BudgetLock` | Daily units are counted per budget (the whole project). Per-minute units are counted per account. The minimum interval is counted per budget. All of it persists across restarts. An exclusive `flock` per budget admits one Gmail command at a time; another exits with code 8 |
| 4. Retries and error classes | `classify`, `error_details`, `_execute` | Waits honor `Retry-After`. Otherwise equal-jitter exponential backoff is used (base 2 s, cap 300 s, at most 6 attempts). A requested wait longer than the cap stops instead of sleeping. Waits are cancellable with Ctrl-C. Stops are distinct and exit nonzero: quota (3), daily budget (3), auth (4), permission (5), request (6), server or network (7), lock (8). Reasons carry the status and the API reason code only, never URLs or messages |
| 5. Recorded termination | `connect.sync` | Every run records `termination`, `reason`, and items processed, with a coverage row. This covers complete, capped, every stop, cancelled (Ctrl-C, re-raised), and error (re-raised, with only the exception type recorded). Stores and the lock are always closed. Cursors move only with processed events; incomplete runs are never marked complete |
| 6. Efficient, correct resume | `GmailConnector.enumerate`, `_listing`, `confirm` | Full syncs save the token of the page being processed and resume there. An invalid token restarts listing from the first page, skipping already-read IDs. Then a reconcile listing pass reads anything the scan missed, and a confirm phase re-reads indexed messages the reconcile listing did not show. Only a confirmed 404 marks absence. Incremental syncs page history and checkpoint after each record |
| 7. Bounded, private progress | `src/towpath/progress.py`, `connect status` | Progress shows phase, counts, waits, and a measured rate with its per-minute spread. `status` shows runs, phase, budget use, and lock state. Neither shows subjects, addresses, message IDs, cursors, page tokens, or URLs |

Also added:

- **Checkpoints:** commits are batched every `checkpoint_every` messages (default 25). A crash loses at most one batch, which the next run re-reads. Ctrl-C and clean stops commit everything.
- **Database upgrade:** existing `source.db` files gain their new columns in place on first use.

### Correctness tradeoff for resume

Gmail page tokens are opaque, and nothing guarantees they stay stable while the mailbox changes. Resuming from a saved token is efficient: it re-lists at most one page instead of every earlier page. But if earlier messages were deleted meanwhile, it can skip some messages.

Towpath therefore pays for one extra listing pass per full sync. It costs 5 units per page of up to 500 IDs, about 200 requests for 100,000 messages. That pass reads anything not yet indexed. Absence then needs a confirming read, 20 units per candidate.

The result:

- A full sync completes only after a whole listing and confirmation.
- A partial scan is never treated as evidence of absence.
- A message moved to Trash or Spam stays indexed with its new labels.

The cost: an extra listing pass, plus one read per previously indexed message that is no longer listed.

## Local validation

Run on the dedicated test account first, after part C of the [local quickstart](../setup/local-quickstart.md). Keep outputs private and publish only generic findings.

1. **Tests:** `python -m pytest -q` reports 94 passed. `ruff check src tests` and the fixture-domain check pass.
2. **Budgets:** confirm the project's quotas in Cloud console. Set `[gmail_pacing]` at or below them; the defaults are fine if the console shows the published new-project values.
3. **Accounting:**
   - Run `towpath connect sync gmail_test --max-items 50`, then `towpath connect status gmail_test`.
   - The `budget.day_used` value should match `sqlite3 state/quota.db "select sum(units) from attempts"`.
   - Request times should be at least 1 s apart: `select max(at) - min(at), count(*) from attempts`.
4. **No burst on restart:** run the same capped command twice back to back. The second run's first request should wait about 1 s.
5. **Lock:** start a sync in one terminal and run `towpath connect probe gmail_test` in another. Expect exit 8 and no traceback.
6. **Ctrl-C:**
   - Interrupt a running sync. Expect exit 130, and `status` shows `cancelled` with the items processed.
   - Note `select max(seq) from attempts` in `state/quota.db`.
   - Run again: the kind is `resumed-full`. Then `select method, count(*) from attempts where seq > <noted value> group by method` should show only one or two `users.messages.list` requests before new reads, not one per page already read.
7. **Quota stops:** if a real quota error occurs, record the exit code (3), the reason (an API reason code with no URL), and the waits seen. Do not deliberately exhaust quota.
8. **Full completion:** let a background run finish, and record the following privately:
   - wall time;
   - the measured rates from the progress lines;
   - total units;
   - the reconcile and confirm phases seen in the progress lines.

   A second run should be `incremental`.
9. **Incremental changes:** on the test account, add a label, archive one message, and delete another. Then run `connect sync`, and check the observations and absence in `source.db`.
10. **Expired authorization:** if the app is still in Testing after 7 days, expect exit 4 with a reauthorization message, then `connect auth` and a resumed run.
11. **D16:** `connect verify-structure --sample 50` still passes, and `select count(*) from cache` stays 0 until a scan requests content.

Record pass or fail for each step in the first-slice results, keeping counts generic. Leave D16 open until steps 8 and 11 pass on the test account.
