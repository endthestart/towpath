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
previews, rebuilt) and the Recoll index (rebuilt by indexing). `files.db`, the file catalog (about 17 GB), is
dumped on the weekly run only.

## On Hub (pending the owner's approval)

- Dataset `zssd/appfast/towpath`, mounted at `/mnt/zssd/appfast/towpath`.
- Dumps: `/mnt/tank/storage/backup/db-dumps/towpath/<stamp>/`, script in `.../towpath/.bin/towpath-dump.py`.
- A TrueNAS cron job (System → Advanced → Cron Jobs), as root, daily at 03:20:

      D=/mnt/tank/storage/backup/db-dumps/towpath; python3 $D/.bin/towpath-dump.py --dataset zssd/appfast/towpath \
        --mount /mnt/zssd/appfast/towpath --dest $D $([ "$(date +%u)" = 7 ] && echo --weekly) && \
        python3 $D/.bin/towpath-dump.py --dest $D --verify

  Sundays include `files.db`. About 0.2 GB a night and 5 GB a week compressed; about 45 GB kept.
- Nothing leaves Hub except through the encrypted offsite copy of `/mnt/tank/storage/backup`.

## Restoring

1. Stop Towpath's services (`docker compose stop`).
2. Pick a dump; `python3 towpath-dump.py --dest <dumps> --verify` checks the newest one.
3. For each store: `gunzip -c <dump>/<store>.db.gz > <data>/state/<store>.db` (keep the old file aside first).
4. Put back `towpath.toml` and `web-login.json` if they were lost, and add each connection's password again on
   Connections (passwords aren't in dumps).
5. Start the services. Rebuildable data (photo previews, the search index) comes back by itself; after a nightly
   dump the file catalog is from the last Sunday until folders are indexed again.
