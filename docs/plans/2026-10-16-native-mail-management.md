# Plan: Towpath-native mail management

Status: proposed 2026-10-16, for owner review together with [ADR 0002](../adrs/0002-towpath-native-mail-management.md).
Branch `claude/native-mail-management`, cut from the deployed head `9cbc7f9`.

## Working rules

- One milestone at a time, finished and qualified before the next. No subagents and no parallel workers.
- Each milestone is its own release to Hub, deployed only while indexing is idle, under the week-plan rules: no
  expensive work at startup, resumable jobs with progress and Pause, rollback tested on schema changes.
- **Done means qualified against the owner's real Fastmail in dry run.** Proposals are reviewed against real mail
  before the owner turns writes on for an action type. Writes are never turned on by a release.
- Tests use fixtures and a fake IMAP server. Live runs use only the read connection until an action type is
  enabled.

## Milestones

| # | Milestone | Done when |
| --- | --- | --- |
| 0 | **Plan and ADR** | Owner approves this plan and ADR 0002. |
| 1 | **Foundation** | A second Fastmail app password is stored as the act credential. `towpath-act` gains an IMAP executor with a command allowlist (STORE flags/keywords, MOVE, APPEND), dry run by default and a per-action-type enable switch. An action ledger holds proposals, approvals, receipts and inverse proposals with Undo. A full-history analysis store is filled by a resumable job (headers, text body with quotes stripped, `List-Unsubscribe`, thread identity). New mail is found by IDLE, with a CONDSTORE catch-up after reconnects. Cloud models appear as an opt-in on the model connections, local by default. A Mail page shows the ledger and the per-type switches. |
| 2 | **Reply tracking and drafts** | Threads are built from `THREADID` or headers. Each thread gets a state (needs reply, awaiting others, FYI, done) from the local model, with the owner's corrections stored. A To reply / Awaiting list with the sanitized reader. Drafts are written to Drafts by APPEND, threaded with `In-Reply-To`, and visible in Fastmail. Qualified on a sample of the owner's real threads. |
| 3 | **Categories and rules** | Categories from header signals first, then the model. Corrections become learned sender patterns. Rules combine static conditions (from, to, subject, body globs; category) with a plain-language condition, ordered and tested against history before saving. Rules propose by default and can be promoted to automatic for reversible actions. Includes the cold-email blocker. Rule history and per-rule accuracy from reviews. |
| 4 | **Cleanup, archive and unsubscribe** | Mail Space by sender, age and size, with the full-history totals. Review lists of old, pointless mail. Archive to an mbox on Hub, read it back and match it message by message, make it searchable, then Trash on approval. Bulk archive (Clean). Bulk unsubscribe: RFC 8058 one-click POST, or `mailto:` sent by SMTP, each batch approved; the sender is then archived by a standing rule. |
| 5 | **Alerts, digest, statistics, filing** | Important mail is flagged or placed where Fastmail notifies. A digest message is APPENDed to the inbox on the owner's schedule. Statistics for volume, senders, rule actions and response times. Attachment filing through the existing destinations. |
| 6 | **Gmail over IMAP and parity** | Gmail connected over IMAP with labels and thread IDs. Dry run alongside Inbox Zero, with a feature-by-feature comparison recorded. The owner decides whether to retire Inbox Zero; D15 is marked superseded. |

## Status

- Milestone 0: approved by the owner on 2026-10-16.
- Milestone 1: deployed 2026-10-17 (`6147799`); Fastmail management on, its whole history read in dry run. Live changes wait for the owner's UAT.
- Milestone 2: built 2026-10-17 (reply tracking and drafts). Continued in the [autonomous build plan](2026-10-17-autonomous-build.md).

## Facts to verify before the milestone that needs them

| Claim | Milestone |
| --- | --- |
| Fastmail's IMAP offers MOVE, UIDPLUS, CONDSTORE/QRESYNC, IDLE, SPECIAL-USE and OBJECTID (`THREADID`, `EMAILID`) | 1 |
| A Fastmail app password can be limited to IMAP plus SMTP, separately from the read-only one | 1 |
| How Fastmail shows IMAP keywords: labels in labels mode, invisible in folders mode | 3 |
| Fastmail empties Trash on its own after a configured retention period | 4 |
| Gmail over IMAP: `X-GM-LABELS`, `X-GM-THRID`, and whether XOAUTH2 or an app password is the supportable sign-in | 6 |
| Which Fastmail folder, flag or contact setting triggers phone notifications | 5 |

## Open items for later review

- Attachment filing was kept in scope; whether cloud drives become a destination type is decided in milestone 5.
- Retention of the analysis store and of the mbox archives, and when they enter the 3-2-1 coverage report.
