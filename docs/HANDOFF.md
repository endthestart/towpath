# Handoff: picking Towpath up again (2026-11-13)

This page stands alone: it is written for a cold start without any earlier session. Private figures, Hub paths and
secrets are in `~/Development/towpath-private/STATUS.md`, which is the private half of this page.

## What Towpath is

A self-hosted workspace for one owner's digital life: mail management over IMAP (dry runs until the owner turns each
kind of change on), a life story built from allowed mail, photos and files (events, memories, people, cited answers),
and search over folders and exports through Recoll. See the [README](../README.md) and the [vision](vision.md). It runs
on the owner's home server (Hub) as three containers pulled by digest in Arcane; GitHub Actions builds, tests and
publishes the images. The repository `endthestart/towpath` is **public**: push only reviewed work, and keep private data
in `towpath-private`.

## Where things stand

- **Hub: stopped.** The owner stopped the Towpath project in Arcane on 2026-11-12 (containers removed, data folder
  kept). The last deployed revision is `96b0122` (deployed 2026-11-06), with its digests in [setup/hub.md](setup/hub.md).
  Nothing runs anywhere else: the development machine runs no Towpath stores, and Hub is the single writer.
- **The *Run once* batch** on the Life page ran 2026-11-06 to 2026-11-08 and ended on its own; finding events by itself
  is off until the owner turns it on again. Its proposals wait for review.
- **Mail stays in dry runs**: nothing has ever changed real mail. Fastmail is the account that is read; the Gmail API
  connection stopped at sign-in during the batch.
- **GitHub:** `main`, `codex/local-email-ui` and `claude/native-mail-management` hold the same work, ending with this
  page. No open pull requests. Images published from `main` after `96b0122` (`3682a06` and any later) changed docs
  only and were never deployed; there is no reason to deploy them.
- **Roadmap:** every planned item is built and reviewed; nothing is in progress and no new features are planned
  ([plan](plans/2026-10-17-autonomous-build.md)). UAT by the owner is the next step.

## Versions

| What | Revision | Where |
| --- | --- | --- |
| Deployed on Hub (now stopped) | `96b0122` | digests in [setup/hub.md](setup/hub.md); Security scan: no Critical or High |
| Latest published, never deployed | `3682a06` | docs only after `96b0122`; scan: 1 High (oauthlib, see below) |
| Source | the three branches above | GitHub; `main` is protected |

## Open items, in priority order

1. **Owner: bring Towpath back on Hub** when wanted: redeploy the `96b0122` digests in Arcane. Nothing else is
   needed; the stores are kept.
2. **Owner: UAT**, following the [UAT guide](plans/2026-10-uat-guide.md): section 0 first (Gmail over IMAP with an app
   password, a week beside Inbox Zero in dry runs), then section 3: the *Run once* proposals newest year first, and the
   2025 events left for review.
3. **Owner: Apple Messages export** (UAT guide, section 5): run `imessage-exporter` on a Mac into the `storage` share,
   then add it on the Connections page.
4. **Next code build: oauthlib 4.0.1 or later.** `uv.lock` pins oauthlib 4.0.0 (CVE-2026-49272). Towpath uses only
   the client sign-in flows, so the flaw is not reachable, but the next build that changes code should bump it, then
   publish, have it scanned and source-checked, and the owner deploys.
5. **Owner decision: two local-only branches.** `claude/poundlock-queue` (`447e441`) makes *Run once* wait behind a
   busy shared model instead of backing off; it passed the full suite but was never pushed, because the owner decides
   whether it goes public. `claude/ubuntu-base-trial` (`899175e`) is an old trial whose change shipped in `bd40db3`; its
   GitHub branch was deleted on the owner's word. Both are kept in a bundle in `towpath-private/cleanup/`. Push,
   or delete the local branches, only on the owner's word.
6. **Owner decision: the old checkout** `towpath-file-discovery-validation` (see below). Delete it only on the owner's
   word, and only together with this one, since it holds their shared git directory.
7. Over the 300-line limit, by the owner's decision of 2026-11-03: `tests/imap_stub.py`,
   `tests/container/test_image.py`, `packaging/release/sources.py`, `packaging/release/publish.py`.

## Owner steps pending

Only the owner does these; sessions hold no Arcane write key and need the owner's own word for pushes to `main`,
releases, Hub changes and deletions.

- Redeploy on Hub when wanted (item 1), and later deploy the oauthlib build (item 4).
- UAT and the Messages export (items 2 and 3).
- Decide the two local branches and the old checkout (items 5 and 6).
- Sign in to the Gmail API connection again, or rely on Gmail over IMAP from UAT section 0.

## Known risks

- **The model host is shared.** Background model work waits when the model answers busy (back-off up to an hour), so
  long batches stall while others use it. Turn finding events by itself on only when that is acceptable.
- **Single writer.** Never point a local Towpath at the real Gmail or Fastmail accounts or at Hub's stores.
- **Backups.** Hub's stores are dumped nightly with a restore check into a folder the encrypted offsite copy covers
  ([backups](setup/backups.md)). `towpath-private` is not under version control: keep a copy, with modes kept.
- **Publishing.** A push to `main` publishes images (so do `v*` tags and a manual run with *publish*). `main` takes a
  commit only after its five CI checks pass on it. CI sometimes fails on Ubuntu archive timeouts: re-run the failed
  jobs (`gh run rerun <id> --failed`).
- **Local runs differ from CI** if the shell sets `PYTHONDONTWRITEBYTECODE=1`: unset it for a run that should match.
- **oauthlib 4.0.0** stays in the deployed images until item 4 ships.

## The checkouts (under `~/Development/towpath/`)

| Folder | Branch | State |
| --- | --- | --- |
| `towpath-mail-management` | `claude/native-mail-management` | The main line; tracks its GitHub branch |
| `towpath-file-discovery-validation` | `claude/file-discovery-foundation` (merged, deleted on GitHub) | Holds the shared git directory and uncommitted notes from early October, superseded and archived byte for byte in `towpath-private/cleanup/old-worktree-notes` |

`~/Development/towpath/` also holds the original planning notes from September (`goals`, `plan`, `research`), outside
git. On a new machine, clone fresh from GitHub instead of moving the checkouts.

## Outside git

| What | Where | Moving it |
| --- | --- | --- |
| `towpath-private` (status, Hub deployment files and release records, secrets, private notes) | `~/Development/towpath-private` | Copy as is, keeping modes (secrets 600); check by checksum |
| Arcane access | The owner deploys in the Arcane UI; sessions read status with a read-only key | Set the read-only key up again on a new machine |
| Towpath's stores, sign-ins and settings | On Hub, in Towpath's data folder | Nothing to move |

## Running, testing and deploying

```bash
git clone https://github.com/endthestart/towpath.git && cd towpath
git switch claude/native-mail-management
uv sync --locked --extra dev
.venv/bin/python -m pytest -q
```

Python 3.11 or newer and [uv](https://docs.astral.sh/uv/). A local demo with made-up data only:
`.venv/bin/towpath web serve --store-dir <empty folder> --port 8799` ([local UI](setup/local-ui.md)).

Releasing: push the reviewed branch to `codex/local-email-ui`, then run the `ci.yml` workflow on it with *publish*,
or fast-forward `main`. Download the release records (`gh run download <run> -n release-core` and `-n release-recoll`),
have the images scanned, run the anonymous source check, and the owner sets both image digests in the Arcane project's
environment and redeploys while nothing is indexing ([containers](setup/containers.md), [Hub](setup/hub.md)). Rolling
back is the same with an earlier pair of digests.

## First three things on resume

1. Read this page and `towpath-private/STATUS.md`; on the development machine run the suite as above.
2. Owner: redeploy `96b0122` on Hub, then start UAT section 0.
3. Make the oauthlib build (item 4) the first code change, and release it as above.

## Working agreement

Dry runs for mail until the owner turns changes on; the hidden photo export and photos hidden in Apple Photos are never
read, shown or previewed, and no cloud model sees photos; owner questions go through the Project Manager session with
options and a recommendation; at most one release a day; keep the UAT guide current; delete scratch files.
