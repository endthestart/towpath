# Handoff prompt for a local agent

Paste the prompt below into a coding agent (for example Claude Code) running on the owner's own machine, in a clone of this repository. It continues work that could not be done in a cloud session without real accounts.

---

You are continuing work on Towpath, an MIT-licensed, self-hosted project in this repository (branch `claude/happy-gauss-ugprmu`). Read `README.md`, then `docs/decisions.md`, `docs/first-slice.md` (including Results), `docs/setup/local-quickstart.md`, and `docs/evaluations/inbox-zero-plan.md` before doing anything.

**What exists.** Every component was tested in a cloud session against synthetic data and stubs; none has met a real service yet:

- A read-only Python CLI (`towpath`) with four SQLite stores and strict roles.
- A Gmail connector using `gmail.readonly` with the owner's own OAuth client.
- A model gateway for OpenAI-compatible endpoints.
- Read-only checksum lookups for Paperless-ngx and Immich.
- A read-only Inbox Zero adapter.

**Your job** is to run that code against the owner's real services, step by step, following `docs/setup/local-quickstart.md`. Report what actually happens and fix defects that the real services reveal.

**Rules you must follow:**

1. **Read only.** Never add code or run commands that change a mailbox, a Paperless or Immich library, or Inbox Zero settings. If a fix seems to need write access, stop and ask the owner.
2. **Private material stays out of Git.** Keep it in `~/towpath-private/` (outside the repository): OAuth client files, tokens, API keys, the config with real URLs, SQLite stores, and command output containing account data. Before every commit, run `git status` and `git diff --cached`. Also run `towpath fixtures check-domains src tests docs examples README.md CONTRIBUTING.md`. Never commit real email addresses, hostnames, IP addresses, file names from real mail, or message content.
3. **Start with a dedicated test Google account.** Move to the owner's primary account only after the owner says so, and only after parts C and D of the quickstart pass.
4. **Separate Google Cloud projects:** one for Towpath (`gmail.readonly` only), another for Inbox Zero. If `towpath connect auth` refuses a token for broader scopes, do not weaken the check. Fix the project instead.
5. **Models:** send nothing beyond what `allow_data` and the owner's grants permit. Use a local endpoint unless the owner configures and grants another.
6. **Before any commit,** run `ruff check src tests` and `python -m pytest -q` with `set -o pipefail` (or check exit codes directly). Never commit with a failing test.
7. **Don't overrule recorded decisions.** If the owner asks for something that conflicts with `docs/decisions.md`, say so and ask before changing a recorded decision.

**Work in this order, and stop after each step to report:**

1. Part A: install, run the tests, and create the private folder.
2. Parts B and C: the Google project, `connect auth`, `connect probe`, and `connect verify-structure --sample 50`. **This settles D16.**
   - If it fails, investigate the field mask in `src/towpath/fieldmask.py` and `GmailConnector`, propose a fix, and add a synthetic fixture case reproducing what Gmail did.
3. Part D: a capped sync of 200 messages, then resume to completion on the test account. Record the messages-per-minute rate. Confirm that a second sync is incremental.
4. Part E: Paperless and Immich probes and scans. Confirm that known files are reported present.
   - If either API differs from what `src/towpath/adapters/destinations.py` expects, fix the adapter. Update `tests/stubs.py` to match the real response shapes, without real data.
5. Part F: probe a local model and run the queue. Record which structured-output methods it supports.
6. Part G: follow `docs/evaluations/inbox-zero-plan.md`, then exercise `towpath provider ...` with a key limited to `STATS_READ` and `RULES_READ`.

**After each step, record:**

- **Private notes** in `~/towpath-private/notes.md`: full outputs.
- **Public summary**, appended to `docs/first-slice.md` Results or a new `docs/evaluations/*.md`: generic findings only, such as "verify-structure passed; max depth 6; inline attachments occurred", or "Paperless 3.2 returned checksum in field X".
- **Decisions:** update `docs/decisions.md` (for example, close D16 with the evidence) and add a decision-log row.

Commit each verified step separately, with a clear message, on the same branch.

**Stop and ask the owner when:**

- a check fails and the cause is unclear;
- a fix needs broader permissions;
- a service needs a setting that weakens security (for example Inbox Zero's `WEBHOOK_ALLOW_PRIVATE_IPS`);
- it is time to move from the test account to the primary account.

---
