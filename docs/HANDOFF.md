# Handoff: continuing Towpath's development (2026-11-01)

## Where things stand

- **Towpath is stopped on Hub.** The owner brought it down on 2026-11-01 and it stays down until they start it again.
  The last deployed revision is `bd40db3`, with its digests in [setup/hub.md](setup/hub.md).
- **`a1d652b` is published, not deployed** (2026-11-02,
  [run 41468454860](https://github.com/endthestart/towpath/actions/runs/41468454860)). Both images passed the anonymous
  source check and a re-scan with no Critical or High findings:
  - `web`: `ghcr.io/endthestart/towpath@sha256:e9b5a2c342aa3589ac0be66092543b6c53897bd11a6095b1d1c428c83343024f`
  - `connect` and `connect-setup`: `ghcr.io/endthestart/towpath-recoll@sha256:169077b5c49a23883cf6421b21c193bbec0633c772d93a3f8312b3f0d6472e4d`
- **GitHub:** `main` is at `a1d652b`; `codex/local-email-ui` and `claude/native-mail-management` are ahead of it with
  *Run once* and these docs. No open pull requests.
- **Local model:** the owner keeps the same machine as the host for finding events and the mail model tasks. Instead of
  weeks of background work, the owner approved one bounded batch: *Run once* on the Life page
  ([life stream](life-stream.md)), planned for 2026-11-06 to 11-08. Finding events by itself is still on in Hub's
  settings, so it starts again after a restart until the owner presses *Stop finding events*.
- **Over the 300-line limit, by the owner's decision of 2026-11-03:** `tests/imap_stub.py`,
  `tests/container/test_image.py`, `packaging/release/sources.py` and `packaging/release/publish.py`.
- **Closing point:** every planned item is built and reviewed, and nothing on the roadmap is left without new scope
  ([plan](plans/2026-10-17-autonomous-build.md)). No new features are planned.
- **UAT is the owner's next step once Towpath is back**, starting with section 0 of the [UAT guide](plans/2026-10-uat-guide.md): connect
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
| Arcane access | Sessions hold no write key: the owner deploys in the Arcane UI, and status uses a separate read-only key | Set up the read-only key again on a new machine |
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

Releasing: push the branch to `codex/local-email-ui` and run the `ci.yml` workflow with `publish=true`. The owner then
deploys the published digests in the Arcane UI while indexing is idle, as [setup/hub.md](setup/hub.md) records.

## Working agreement (unchanged)

Dry runs for mail; private-content rules as above; one task at a time with at most one reviewer; owner questions
through the Project Manager session, each with options and a recommendation; release under the owner's grant;
keep the UAT guide current; delete scratch files.
