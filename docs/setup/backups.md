# Backing up Towpath's databases

Towpath keeps its state in SQLite stores (`state/*.db` in its data folder), not Postgres. A copy of a live store
isn't a backup, and ZFS snapshots on the same pool don't survive the pool. So a nightly job on the host makes
consistent, checked dumps into a folder the encrypted offsite copy covers.

## The job

[`deploy/backup/towpath-dump.py`](../../deploy/backup/towpath-dump.py), Python's standard library only, run by the
host's cron as root:

1. A ZFS snapshot of the data folder's dataset, so the live stores are never locked or paused.
2. Each store is copied out of the snapshot (with its journal if a write was under way). Opening the copy rolls an
   unfinished write back, as SQLite does after a crash. `PRAGMA integrity_check` must pass. `VACUUM INTO` writes a
   compact copy, which is gzipped.
3. `<dest>/<stamp>/` gets one `<store>.db.gz` per store, `towpath.toml`, the sign-in file and a `manifest.json`
   (sizes, SHA-256, checks), mode 0700/0600. A failed run leaves no partial dump behind.
4. The snapshot and the work folder are removed, and old dumps are pruned: the last 14 nightly, the last 8 weekly,
   and the first dump of each of the last 6 months.
5. `--verify` then restores the newest dump into a throwaway folder: every checksum must match its manifest, and
   every store must decompress, open and pass its integrity check.

Left out: `credentials/` (passwords stay out of backups; re-enter them after a restore), `thumbs.db` (photo
previews, rebuilt) and the Recoll index (rebuilt by indexing). `files.db`, the file catalog, is dumped on the
weekly run only.

## On Hub (installed 2026-10-19, the owner's approval relayed by the Project Manager)

- A nightly TrueNAS cron job, as root (errors are mailed; normal output isn't). Sundays include `files.db`:

      D=<dumps folder>; python3 $D/.bin/towpath-dump.py --dataset <dataset> \
        --mount <data folder> --dest $D $([ "$(date +%u)" = 7 ] && echo --weekly) && \
        python3 $D/.bin/towpath-dump.py --dest $D --verify

- The datasets' NFSv4 ACLs refuse `chmod`, so the dumps keep the folder's inherited ACL (root and its group only),
  as the other dumps there do.
- Nothing leaves Hub except through the encrypted offsite copy of the backup folder.
- The host's paths and sizes are kept with the private deployment files.

## Restoring

1. Stop Towpath's services (`docker compose stop`).
2. Pick a dump; `python3 towpath-dump.py --dest <dumps> --verify` checks the newest one.
3. For each store: `gunzip -c <dump>/<store>.db.gz > <data>/state/<store>.db` (keep the old file aside first).
4. Put back `towpath.toml` and `web-login.json` if they were lost, and add each connection's password again on
   Connections (passwords aren't in dumps).
5. Start the services. Rebuildable data (photo previews, the search index) comes back by itself; after a nightly
   dump the file catalog is from the last Sunday until folders are indexed again.
