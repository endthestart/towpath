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
  kept), because running it takes too much of the server. The last deployed revision is `96b0122` (deployed
  2026-11-06), with its digests in [setup/hub.md](setup/hub.md). Nothing runs anywhere else: the development machine
  runs no Towpath stores, and Hub is the single writer.
- **Release `v0.1.0`** is the first tagged release: oauthlib 4.0.1 (clearing CVE-2026-49272, which Towpath never
  reached) and the Towpath icon. It is published from `main`, with its image digests on the
  [GitHub release](https://github.com/endthestart/towpath/releases/tag/v0.1.0), and not yet deployed.
- **The *Run once* batch** on the Life page ran 2026-11-06 to 2026-11-08 and ended on its own; finding events by itself
  is off until the owner turns it on again. Its proposals wait for review.
- **Mail stays in dry runs**: nothing has ever changed real mail. Fastmail is the account that is read; the Gmail API
  connection stopped at sign-in during the batch.
- **GitHub:** `main`, `codex/local-email-ui` and `claude/native-mail-management` hold the same work, at `v0.1.0`. No
  open pull requests and no other branches.
- **Roadmap:** every planned feature is built and reviewed; no new features are planned
  ([plan](plans/2026-10-17-autonomous-build.md)).

## Open items, in priority order

1. **`0.1.1`: performance.** On Hub, Towpath uses so much of the server that the owner keeps turning it off. Measure
   what each service does while it runs (CPU, memory, disk, the Recoll index, background model work), find what
   starves the server, and cap or schedule it so Towpath can stay on. Owner step: run Towpath on Hub while it is
   measured.
2. **Owner: deploy `v0.1.0`** when bringing Towpath back: set its two digests in the Arcane project's environment and
   redeploy. Nothing else is needed; the stores are kept. Best done together with item 1.
3. **Owner: UAT**, following the [UAT guide](plans/2026-10-uat-guide.md): section 0 first (Gmail over IMAP with an app
   password, a week beside Inbox Zero in dry runs), then section 3: the *Run once* proposals newest year first, and the
   2025 events left for review.
4. **Owner: Apple Messages export** (UAT guide, section 5): run `imessage-exporter` on a Mac into the `storage` share,
   then add it on the Connections page.
5. **The model host.** Towpath's model tasks (mail categories, drafts, life events) use a model on the owner's Mac Pro.
   With that machine off they wait and retry without breaking, but nothing tells the owner. Choosing the model host is
   the owner's decision; then test Towpath with the model unreachable and say so in the UI if needed.
6. Over the 300-line limit, by the owner's decision of 2026-11-03: `tests/imap_stub.py`,
   `tests/container/test_image.py`, `packaging/release/sources.py`, `packaging/release/publish.py`.

## Owner steps pending

Only the owner does these; sessions hold no Arcane write key and need the owner's own word for pushes to `main`,
releases, Hub changes and deletions.

- Deploy `v0.1.0` (item 2) and run Towpath while it is measured (item 1).
- UAT and the Messages export (items 3 and 4).
- Choose the model host (item 5).
- Sign in to the Gmail API connection again, or rely on Gmail over IMAP from UAT section 0.

## Known risks

- **Resource use on Hub.** Running Towpath can bring the server down (item 1). Until that is fixed, start it only while
  someone watches, and stop it in Arcane if the server slows.
- **The model host is shared.** Background model work waits when the model answers busy (back-off up to an hour), so
  long batches stall while others use it. Turn finding events by itself on only when that is acceptable.
- **Single writer.** Never point a local Towpath at the real Gmail or Fastmail accounts or at Hub's stores.
- **Backups.** Hub's stores are dumped nightly with a restore check into a folder the encrypted offsite copy covers
  ([backups](setup/backups.md)). `towpath-private` is not under version control: keep a copy, with modes kept.
- **Publishing.** A push to `main` publishes images (so do `v*` tags and a manual run with *publish*). `main` takes a
  commit only after its five CI checks pass on it. CI sometimes fails on Ubuntu archive timeouts: re-run the failed
  jobs (`gh run rerun <id> --failed`).
- **Local runs differ from CI** if the shell sets `PYTHONDONTWRITEBYTECODE=1`: unset it for a run that should match.

## The checkout and outside git

One clone at `~/Development/towpath/towpath`, on `claude/native-mail-management`. The earlier worktrees were retired on
2026-11-13; their notes and branches are archived in `towpath-private/cleanup/`. `~/Development/towpath/` also holds
the original planning notes from September (`goals`, `plan`, `research`), outside git.

| What | Where | Moving it |
| --- | --- | --- |
| `towpath-private` (status, Hub deployment files and release records, secrets, private notes) | `~/Development/towpath-private` | Copy as is, keeping modes (secrets 600); check by checksum |
| Arcane access | The owner deploys in the Arcane UI; sessions read status with a read-only key | Set the read-only key up again on a new machine |
| Towpath's stores, sign-ins and settings | On Hub, in Towpath's data folder | Nothing to move |

## Running, testing and releasing

```bash
git clone https://github.com/endthestart/towpath.git && cd towpath
git switch claude/native-mail-management
uv sync --locked --extra dev
.venv/bin/python -m pytest -q
```

Python 3.11 or newer and [uv](https://docs.astral.sh/uv/). A local demo with made-up data only:
`.venv/bin/towpath web serve --store-dir <empty folder> --port 8799` ([local UI](setup/local-ui.md)).

Releasing, with the owner's word: set the version in `pyproject.toml` and `src/towpath/__init__.py` (then `uv lock`),
push the reviewed branch to `codex/local-email-ui`, and once CI passes fast-forward `main`, which publishes. Tag that
commit `vX.Y.Z`, push the tag, and create the GitHub release with the digests from the release records
(`gh run download <run> -n release-core` and `-n release-recoll`). Have the images scanned and run the anonymous
source check; the owner then sets both digests in the Arcane project's environment and redeploys while nothing is
indexing ([containers](setup/containers.md), [Hub](setup/hub.md)). Rolling back is the same with an earlier pair of
digests.

## First three things on resume

1. Read this page and `towpath-private/STATUS.md`; run the suite as above.
2. Plan `0.1.1` (item 1) with the owner, who deploys `v0.1.0` on Hub while it is measured.
3. Once Towpath runs without straining the server, the owner starts UAT section 0.

## Working agreement

Dry runs for mail until the owner turns changes on; the hidden photo export and photos hidden in Apple Photos are never
read, shown or previewed, and no cloud model sees photos; owner questions go through the Project Manager session with
options and a recommendation; at most one release a day; keep the UAT guide current; delete scratch files.
