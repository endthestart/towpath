# Handoff: continuing Towpath's development (2026-11-01)

## Where things stand

- **Towpath is deployed on Hub through Arcane**; the deployed revision and image digests are in
  [setup/hub.md](setup/hub.md). Every planned item is built, reviewed and deployed; nothing on the roadmap is left
  without new scope ([plan](plans/2026-10-17-autonomous-build.md)). The project is being brought to a closing point:
  no new features are planned.
- **UAT is the owner's next step**, starting with section 0 of the [UAT guide](plans/2026-10-uat-guide.md): connect
  Gmail over IMAP with an app password and run Towpath beside Inbox Zero for a week, Towpath in **Dry runs only**.
  Inbox Zero's rules are rewritten as a switched-off set on each account's Rules page, for the owner to confirm.
- Mail stays in dry runs; nothing has changed real mail. Private-content rules hold: no cloud model on photos; the
  hidden photo export and photos hidden in Apple Photos are never read, shown or previewed.
- Hub database dumps run nightly ([setup/backups.md](setup/backups.md)).

## The checkouts (under `~/Development/towpath/`)

Both worktrees share one git directory, inside `towpath-file-discovery-validation/.git`, so that folder has to move
with the other. Virtual environments don't move (see *Running*).

| Folder | Branch | What it is for | State |
| --- | --- | --- | --- |
| `towpath-mail-management` | `claude/native-mail-management` | The main line of work. Pushed as `codex/local-email-ui` too, which publishing builds from. | On GitHub |
| `towpath-file-discovery-validation` | `claude/file-discovery-foundation` | File discovery and Recoll validation, merged into the main line; holds the shared git directory | Uncommitted evaluation notes and a Recoll provider edit, also archived in `towpath-private` |

The older worktrees and the earlier separate clone were retired on 2026-11-01. Their uncommitted notes, and a bundle
of the clone's unpushed commits, are in `towpath-private/cleanup/old-worktree-notes`.

The GitHub repository `endthestart/towpath` is **public**; push only reviewed work there.

## Outside git

| What | Where | Moving it |
| --- | --- | --- |
| `towpath-private` (Hub deployment files and env, secrets, private notes, retired stores) | `~/Development/towpath-private`, not a git repository | Copy as is, keeping modes (secrets 600), and check by checksum |
| The Arcane API key | The development machine's credential store | Set up again on a new machine; the deployment helper in `towpath-private` reads it from there |
| Gmail and quota configuration, Towpath's stores | On Hub, the single writer since 2026-10-06 | Nothing to move; the development machine runs no Towpath stores |
| Apple Messages export | On the NAS once the owner runs imessage-exporter, see the UAT guide | Nothing to move |

## Running Towpath on the development machine

Development and tests (Python 3.11 or newer with uv, from a checkout):

```bash
cd ~/Development/towpath/towpath-mail-management
uv sync --locked --extra dev
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
