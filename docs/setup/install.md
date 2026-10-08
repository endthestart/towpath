# Install Towpath

For one owner on a home server with Docker Compose (for example TrueNAS with Arcane) and an existing HTTPS
reverse proxy (for example SWAG). No shell is needed. Accounts, folders, passwords and indexing are all set
up in the browser afterwards.

## 1. Create the data folder

Towpath keeps everything in one folder and creates what it needs inside it on first start.

On TrueNAS: **Datasets**, select the dataset that holds your app data, **Add Dataset**, name it `towpath`,
and choose the **Apps** preset. That makes the `apps` user (ID 568), which the containers run as by default,
its owner.

All apps that run as `apps` can read each other's files, including the passwords and tokens Towpath stores.
To keep those private to Towpath, create a dedicated user instead (**Credentials → Users → Add**, for example
ID 10001, no login), make it the dataset's owner with **Edit Permissions**, and set `TOWPATH_USER` below to
that ID.

## 2. Route the address to Towpath

Choose an address such as `https://towpath.example.org` that your proxy can serve with a valid certificate.
For SWAG, copy [`deploy/swag-towpath.subdomain.conf.example`](../../deploy/swag-towpath.subdomain.conf.example)
into SWAG's `nginx/proxy-confs/` folder as `towpath.subdomain.conf`; SWAG reloads it automatically. It sends
`/connections/` to the connector and everything else to the web service. Towpath publishes no port of its own.

## 3. Start Towpath

In Arcane, create a project named `towpath`. Paste [`deploy/compose.hub.example.yml`](../../deploy/compose.hub.example.yml)
as its Compose file and [`deploy/env.hub.example`](../../deploy/env.hub.example) as its environment, then fill
in the environment:

| Setting | Value |
| --- | --- |
| `TOWPATH_IMAGE`, `TOWPATH_RECOLL_IMAGE` | The published image digests for the release you want ([containers](containers.md)) |
| `TOWPATH_DATA_DIR` | The dataset's path, for example `/mnt/pool/apps/towpath` |
| `TOWPATH_PUBLIC_URL` | The address from step 2 |
| `TOWPATH_PROXY_NETWORK` | Your proxy's Docker network (SWAG's is often `proxy`) |
| `TOWPATH_USER` | `568:568`, or your dedicated user from step 1 |

Deploy. The connector starts first and creates the folder's layout; the web service follows. If the folder
can't be written, the `connect-setup` log says what to change.

## 4. Create your account

Open the address. Towpath asks for a setup code: in Arcane, open the `web` container's log and copy the line
*Towpath first-run setup code*. Choose a username and a password of at least 12 characters. This one account
can do everything; there are no other users.

## 5. Add accounts

Open **Connections** and add Fastmail, Gmail or another mail account. Each page explains what to do at the
provider. Nothing is indexed until you choose **Start indexing**.

## 6. Index folders (optional)

Towpath can index folders on the same server: names, types, sizes and dates of every file, and the text of
documents. It reads them only, through a read-only mount, and never changes, moves or deletes anything.

1. Set `TOWPATH_LIBRARY_DIR` to the folder that contains what you want indexed (on TrueNAS, a dataset or its
   parent). To mount several datasets, give the connector services one read-only line each under `/library`.
2. The containers' user must be able to read those folders. If it can't, create a group in TrueNAS
   (**Credentials → Groups → Add**), give it **Read** with **Inherit** on each dataset (**Edit Permissions**),
   and set `TOWPATH_READ_GROUP` to its ID. Only Towpath's connector services join that group.
3. Redeploy, open **Connections → Folders on this server**, tick folders and choose **Save and start indexing**.
   Every file is listed, including caches, recycle bins and system folders. Photos, video, music and files
   Towpath doesn't recognise are indexed by name; documents by their text too. Indexing shows its progress, can
   be paused, and picks up changes when you index again. **How Towpath indexes these folders** on the same page
   sets how many files are read at once (2 by default, gentle on spinning disks; raise it for SSDs), which types
   are indexed by name only, what to skip, and size limits.
4. When indexing finishes, **Space** shows where the room goes: folder sizes, space by type, the largest files,
   and clutter that is usually safe to clean up (package folders, caches, recycle bins, system copies, old
   backup sets). It only reads the index; deleting is up to you.

## Storage

Towpath's data folder holds SQLite stores and, with folders indexed, Recoll's index, which grows to tens of GB
for millions of files. Both are written in small pages (4 KB for SQLite, 8 KB for Recoll's Xapian index), so:

- **Put the data folder on SSD.** On spinning disks a large index spends hours on each batch write.
- **Set the dataset's record size to 16K** (**Datasets → Edit → Advanced Options → Record Size**). With the
  default 128K, ZFS rewrites a whole 128 KB block for every small page, several times the real writes. The
  setting applies to files written afterwards: copy existing files once (with Towpath stopped) to rewrite them.
- Media and backups are the opposite: written once and read straight through, they are best left at 128K or
  set to 1M. Small files gain nothing from a smaller record size; they are already stored in one small block.

Even tuned, Recoll's first pass over millions of files takes many hours: each batch of new text is merged into
the whole index. Later passes only touch what changed.

## Updating, rolling back and backing up

- **Update:** put the new release's two digests in the environment and redeploy.
- **Roll back:** put the previous digests back and redeploy. Your data folder is unchanged by either.
- **Back up:** snapshot the dataset (TrueNAS **Data Protection → Periodic Snapshot Tasks**) and, for an
  off-site copy, replicate it. The `state/` folder holds the indexes and your decisions; `credentials/` holds
  sign-ins, which you can also recreate by reconnecting.
- **Forgotten password** (the one recovery step that needs a command): in Arcane, open the `web` container's
  console and run `towpath web reset-login --store-dir /data/state --yes`, then open the site and use the new
  setup code that appears in the log.

## What lives where

| In the data folder | Holds | Seen by |
| --- | --- | --- |
| `state/` | Indexes, collections, requests, quota accounting, the login hash and session key | all services |
| `credentials/` | Passwords and tokens added on the Connections page, one file each, owner-only | the two connector services |
| `index/`, `scratch/` | Search indexes and temporary files for long jobs | the connector services |
| `towpath.toml` | Optional settings not yet on a page (such as file discovery) | the connector services |
