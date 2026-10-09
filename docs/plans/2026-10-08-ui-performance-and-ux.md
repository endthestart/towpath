# UI performance and UX at catalog scale (2026-10-08)

Folders indexing on Hub covers about 7.1 million files. The UI and catalog were built and tested against
thousands, so this plan records what breaks at that scale, what has been changed, and what comes next. The owner
asked for everything to be indexed so that space can be reclaimed; first-scan time doesn't matter, load on the
server does.

## Measurements

Synthetic catalog of 1 million invented paths (Mac SSD); Hub holds about seven times as many rows. Hub figures
are from the live services.

| Path | Before | After | Change |
| --- | --- | --- | --- |
| Recoll on Hyper Backup chunk files | ~10 files/s, ~290 MB/s pool reads | ~3,600 files/s, ~4 MB/s | Unrecognised types listed by name without opening them to guess (`usesystemfilecommand = 0`) |
| Import listing (Recoll → catalog), 7 M files | 13+ h, quadratic: query paging re-ranks every 50 rows | ~6 min of Recoll reads (49 µs per record) | Walk record-ID terms with the Xapian binding, cursor between pages |
| Catalog search, broad word (100–300 k matches) | 240–490 ms; 0.5–4 s for the sorted `IN` form | ~1 ms; 40 ms at offset 20,000 | Trigram index drives the join in catalog order, no sort of every match |
| Files status (twice per Search page) | 1.9 s per 1 M rows (≈13 s at Hub scale) | one row per root | Counts by status kept after each import (`catalog_counts`) |
| Mail status on Hub (IMAP 115 k messages) | 0.72 s | index-only count | `items (source_id, absent_since_run)` index, one query |
| Folders page while indexing | full reload every 5 s (763/h); Safari reset the tab, and the settings form couldn't be used | only the progress panel updates | htmx 2.0.9 (vendored, pinned, as in senex-trader) polls a fragment; CSP `script-src 'self'` |

Catalog size: about 880 MB per million rows with the trigram index (≈6 GB at Hub scale). Inserts run at about
12,800 rows/s on SSD. Measuring space for the same million rows takes 57 s and 450 MB; the synthetic paths are
unique at every level, so they make 5 million folders, far more than real trees.

## Observations

- Recoll still opens each file it doesn't recognise by name for a few kilobytes (its mbox check). On the HDD
  pool that is random I/O, about 70 files/s inside Hyper Backup's internal folders. It is acceptable for a first
  scan. `ionice` has no effect on ZFS, so Recoll's reads compete equally with other apps; the queue depth comes
  from Recoll's thread counts. If load matters, use one worker thread, or index backup-set folders
  (`*.hbk`, `*.sparsebundle`, `Backups.backupdb`) by name only through per-folder sections in `recoll.conf`.
- The import used to run inside the worker's poll, so mail checks waited for it (about an hour at Hub scale).
  Adding to search and measuring space now run in a background thread beside the loop.
- The Overview page counted mail labels on every load (1.8 s of its 2.2 s on Hub, a join over every message).
  The counts are now kept until the mail store changes.
- Mail catalog search scans subjects and senders with `LIKE` (about 0.3 s per source on Hub). The trigram
  approach used for files applies here too once mail grows.
- Recoll's index outgrew memory on spinning disks: at 41 GB (24 GB of word positions) each 50 MB batch merged
  into the whole index, so Recoll spent hours flushing at about 1.5 documents/s and had written 418 GB in total.
  Batches are now 512 MB by default (a setting on the Folders page), and putting the index folder on SSD
  storage would help most.
- Moved to an NVMe mirror, the index still took 7+ minutes per batch: with a 128K record size the pool wrote
  113–145 MB/s (both drives together) for Recoll's 13 MB/s. Rewritten at 16K, two batches took 6.3 and 3.6
  minutes with the pool writing 84 and 145 MB/s for Recoll's 32 and 42 MB/s (1.3–1.7× per drive). The rest of
  each batch is Xapian's own work: merging 512 MB of new text rewrote 9–12 GB of a 44 GB index. Larger batches
  are the remaining lever (fewer merges), at the cost of memory.
- Recoll finished its first pass at 6.95 M files (4.12 M documents). Adding them to search then ran at about 430
  files/s: the safety check that a reference stays inside its root followed every path to its real location,
  one metadata read per file on the spinning pool, at queue depth one. The import now resolves and lists each
  folder once (directory entries carry their type, so files' own metadata isn't read) and follows only
  symbolic links; reads still resolve each path in full. A worker restart while adding used to start Recoll's
  whole walk again; it now carries on adding.
- With that fixed, the import ran CPU-bound at about 880 files/s on one core. Profiling (synthetic rows) put
  70% of the time in the reference check's `pathlib` work, done twice per row with the root resolved each
  time; as string operations on normalised paths it runs about 2.4 times faster (`bc0670b`).
- After 6.2 M rows of the first root, recounting the root by status failed: `GROUP BY` over every row sorts
  them, and SQLite's temporary files live in the connector's 64 MB `/tmp`. The counts now stream through a
  counter. Queries over the whole catalog must not sort it (duplicates use `temp_store = MEMORY` on a smaller
  set). After a failure or a pause while adding, Continue now carries on adding instead of re-running Recoll's
  check (about 70 minutes of metadata reads on Hub).
- ZFS counts objects, not files: `df -i` gave 7.1 M for the four datasets, Recoll walked 3.03 M files.
- Files that Recoll couldn't read (149 of the first 1.5 M) are mostly CHM help files with no pages, RAR archives,
  and damaged or cloud-placeholder Office files.

## Next, in order

1. **Space view** (built; see `towpath/space.py` and the Space page). The reason everything is indexed is to
   find what can be deleted. After each import the
   connector computes, from the catalog alone and without touching files: folder sizes rolled up the tree,
   largest files, space by type, and common clutter (package folders such as `node_modules`, `.git` history,
   caches, thumbnail and metadata files, recycle bins, Windows and Linux system folders, temp files and backup
   sets). The page drills down by folder, and every figure links to the matching search. It is read-only; it
   never deletes or moves anything.
2. **File rows and details.** Show each file's size (done) and folder in results. A file page with path, size, dates and
   type, and a link to its folder in Space.
3. **File filters.** `in:<folder>`, `size:>100MB`, and modified-date ranges; type chips for common kinds.
4. **Import resumable after a restart** from the cursor (it runs in the background now, but starts over).
5. **Mail subjects and senders through a trigram index**, as for files.
6. **Duplicates.** By name and size (built: files of 1 MB or more, across all folders, backup-set chunks left
   out; about 5 s per million rows). By content hash later: that needs a read-only hashing pass that is
   scheduled and throttled.
7. Static assets cached under versioned URLs (minor).
