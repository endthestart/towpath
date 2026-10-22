# Handoff: moving Towpath's development to the Mac Pro (2026-10-21)

## Where things stand

- **Hub runs Towpath** (Arcane deployment); deployed revision and image digests are in
  [setup/hub.md](setup/hub.md). Every planned item is built, reviewed and deployed; nothing on the roadmap is left
  without new scope ([plan](plans/2026-10-17-autonomous-build.md)).
- **UAT is the owner's next step**, starting with section 0 of the [UAT guide](plans/2026-10-uat-guide.md): connect
  Gmail over IMAP with an app password and run Towpath beside Inbox Zero for a week, Towpath in **Dry runs only**.
  Inbox Zero's rules are rewritten as a switched-off set on each account's Rules page, for the owner to confirm.
- Mail stays in dry runs; nothing has changed real mail. Private-content rules hold: no cloud model on photos; the
  hidden photo export and photos hidden in Apple Photos are never read, shown or previewed.
- Hub database dumps run nightly ([setup/backups.md](setup/backups.md)).

## The checkouts (all under `~/Development/towpath/`, copied to the Mac Pro on 2026-10-21)

The five worktrees share one git directory, inside `towpath-file-discovery-validation/.git`; paths are the same
on both Macs, so they work as they are. Their virtual environments were not copied (see *Running*).

| Folder | Branch | What it is for | State |
| --- | --- | --- | --- |
| `towpath-mail-management` | `claude/native-mail-management` | The main line of work since 2026-10-16: mail management, life story, photos, sources, backups. Pushed as `codex/local-email-ui` too, which publishing builds from. | Clean, on GitHub |
| `towpath-local-ui` | `codex/local-email-ui` | Older checkout of the publishing branch (the main line has moved on in `towpath-mail-management`) | Uncommitted roadmap note and a research note |
| `towpath-file-discovery-validation` | `claude/file-discovery-foundation` | File discovery and Recoll validation (holds the shared git directory) | Uncommitted evaluation notes and a Recoll provider edit |
| `towpath-release-review` | `codex/ghcr-publication-handoff` | Container publishing review (merged into `main`) | Uncommitted follow-up note and a handoff |
| `towpath-unified-review` | `codex/unified-local-review-fixes` | Unified-review fixes; 4 commits not on GitHub | Clean |
| `towpath` (separate clone) | `claude/happy-gauss-ugprmu` | Earliest Gmail-connector work | The Mac Pro already had this clone; its 4 unpushed commits and `codex/local-prevalidation-082b4b5` are in `~/Development/towpath/macbook-clone-branches.bundle` (`git fetch ../macbook-clone-branches.bundle '*:refs/remotes/macbook/*'`), its two untracked notes copied in |

The GitHub repository `endthestart/towpath` is **public**. The branches not on GitHub stay local, on both Macs
(the owner's answer of 2026-10-21); push only reviewed work there.

## Outside git

| What | Where | Moving it |
| --- | --- | --- |
| `towpath-private` (Hub deployment files and env, secrets such as a Google OAuth client and an old Mac Gmail token, private notes, backups of the retired Mac stores) | `~/Development/towpath-private`, not a git repository | Copied to the Mac Pro as is on 2026-10-21 (the owner's choice; modes kept, secrets 600, checked by checksum) |
| Gmail and quota configuration, Towpath's stores | On Hub (`/mnt/zssd/appfast/towpath`), the single writer since 2026-10-06 | Nothing to move; the Mac runs no Towpath stores |
| Local databases | None in the checkouts; the retired Mac stores are in `towpath-private/backups` and `retired-handed-to-hub-20261006` | With `towpath-private` |
| Apple Messages export | On the NAS (`storage/exports/messages`) once the owner runs imessage-exporter, see the UAT guide | Nothing to move |
| Colima profile `towpath-files-validation` | Already deleted on the MacBook | Nothing to move |
| Hub tools (release, smoke test) | Session scratch on the MacBook | Re-create from [setup/hub.md](setup/hub.md) if needed |

## Running Towpath on the Mac Pro

Development and tests (Python 3.13 with uv, from a checkout):

```bash
cd ~/Development/towpath/towpath-mail-management
uv venv .venv && uv pip install -e '.[dev]'
.venv/bin/python -m pytest -q
```

A local demo with made-up data only: `.venv/bin/towpath web serve --store-dir <empty folder> --port 8799`
(see [setup/local-ui.md](setup/local-ui.md)). Never point a local Towpath at the real Gmail or Fastmail
accounts or at Hub's stores: Hub is the single writer.

Releasing: push the branch to `codex/local-email-ui`, run the `ci.yml` workflow with `publish=true`, then deploy
the published digests to Hub while indexing is idle, as [setup/hub.md](setup/hub.md) records.

## Working agreement (unchanged)

Dry runs for mail; private-content rules as above; one task at a time with at most one reviewer; owner questions
through the Project Manager session, each with options and a recommendation; release under the owner's grant;
keep the UAT guide current; delete scratch files.
