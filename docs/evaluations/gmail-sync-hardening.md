# Gmail sync hardening: implementation handoff

Status: implementation requested; live full-mailbox and incremental validation pending.

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
