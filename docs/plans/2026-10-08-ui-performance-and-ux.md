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
| Folders page while indexing | full reload every 5 s (763/h); Safari reset the tab | in-place update | One same-origin script; CSP `script-src 'self'` |

Catalog size: about 880 MB per million rows with the trigram index (≈6 GB at Hub scale). Inserts run at about
12,800 rows/s on SSD. Measuring space for the same million rows takes 57 s and 450 MB; the synthetic paths are
unique at every level, so they make 5 million folders, far more than real trees.

## Observations

- Recoll still opens each file it doesn't recognise by name for a few kilobytes (its mbox check). On the HDD
  pool that is random I/O, about 70 files/s inside Hyper Backup's internal folders. It is acceptable for a first
  scan. `ionice` has no effect on ZFS, so Recoll's reads compete equally with other apps; the queue depth comes
  from Recoll's thread counts. If load matters, use one worker thread, or index backup-set folders
  (`*.hbk`, `*.sparsebundle`, `Backups.backupdb`) by name only through per-folder sections in `recoll.conf`.
- The import runs inside the worker's poll, so mail checks wait for it (about an hour at Hub scale). It should
  run in slices that resume from the cursor.
- Mail catalog search scans subjects and senders with `LIKE` (about 0.3 s per source on Hub). The trigram
  approach used for files applies here too once mail grows.
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
4. **Import in slices** that resume from the cursor, so mail checks never wait for a large import.
5. **Mail subjects and senders through a trigram index**, as for files.
6. **Duplicates.** By name and size first (from the catalog). By content hash later: that needs a read-only
   hashing pass that is scheduled and throttled.
7. Static assets cached under versioned URLs (minor).
