# UI performance and UX at catalog scale (2026-10-08)

Folders indexing on Hub covers millions of files. The UI and catalog were built and tested against
thousands, so this plan records what breaks at that scale, what has been changed, and what comes next. The owner
asked for everything to be indexed so that space can be reclaimed; first-scan time doesn't matter, load on the
server does.

## Measurements

Synthetic catalog of 1 million invented paths (Mac SSD). Hub's own figures are kept privately.

| Path | Before | After | Change |
| --- | --- | --- | --- |
| Recoll on Hyper Backup chunk files | each file opened to guess its type, saturating the pool's reads | far faster, with light reads | Unrecognised types listed by name without opening them to guess (`usesystemfilecommand = 0`) |
| Import listing (Recoll → catalog) | quadratic: query paging re-ranks every 50 rows | linear | Walk record-ID terms with the Xapian binding, cursor between pages |
| Catalog search, broad word (100–300 k matches) | 240–490 ms; 0.5–4 s for the sorted `IN` form | ~1 ms; 40 ms at offset 20,000 | Trigram index drives the join in catalog order, no sort of every match |
| Files status (twice per Search page) | 1.9 s per 1 M rows | one row per root | Counts by status kept after each import (`catalog_counts`) |
| Mail status (a large IMAP mailbox) | most of a second | index-only count | `items (source_id, absent_since_run)` index, one query |
| Folders page while indexing | full reload every 5 s (763/h); Safari reset the tab, and the settings form couldn't be used | only the progress panel updates | htmx 2.0.9 (vendored, pinned, as in senex-trader) polls a fragment; CSP `script-src 'self'` |

Catalog size: about 880 MB per million rows with the trigram index. Inserts run at about 12,800 rows/s on SSD.
Measuring space for the same million rows takes 57 s and 450 MB; the synthetic paths are unique at every level,
so they make 5 million folders, far more than real trees.

## Observations

- Recoll still opens each file it doesn't recognise by name for a few kilobytes (its mbox check). On the HDD
  pool that is slow random I/O inside Hyper Backup's internal folders. It is acceptable for a first
  scan. `ionice` has no effect on ZFS, so Recoll's reads compete equally with other apps; the queue depth comes
  from Recoll's thread counts. If load matters, use one worker thread, or index backup-set folders
  (`*.hbk`, `*.sparsebundle`, `Backups.backupdb`) by name only through per-folder sections in `recoll.conf`.
- The import used to run inside the worker's poll, so mail checks waited for it. Adding to search and measuring
  space now run in a background thread beside the loop.
- The Overview page counted mail labels on every load (most of its load time on Hub, a join over every message).
  The counts are now kept until the mail store changes.
- Mail catalog search scans subjects and senders with `LIKE`. The trigram approach used for files applies here
  too once mail grows.
- Recoll's index outgrew memory on spinning disks: once it was many times larger than memory, each 50 MB batch
  merged into the whole index, so Recoll spent hours flushing and wrote far more than it indexed.
  Batches are now 512 MB by default (a setting on the Folders page), and putting the index folder on SSD
  storage would help most.
- Moved to an NVMe mirror, batches were still slow: with a 128K record size the pool wrote several times what
  Recoll wrote. Rewritten at 16K, each drive wrote less than twice Recoll's amount. The rest of each batch is
  Xapian's own work: merging a batch rewrites much of the index. Larger batches are the remaining lever (fewer
  merges), at the cost of memory.
- When Recoll finished its first pass, adding its records to search was slow: the safety check that a reference
  stays inside its root followed every path to its real location, one metadata read per file on the spinning
  pool, at queue depth one. The import now resolves and lists each folder once (directory entries carry their
  type, so files' own metadata isn't read) and follows only symbolic links; reads still resolve each path in
  full. A worker restart while adding used to start Recoll's whole walk again; it now carries on adding.
- With that fixed, the import ran CPU-bound on one core. Profiling (synthetic rows) put 70% of the time in the
  reference check's `pathlib` work, done twice per row with the root resolved each time; as string operations
  on normalised paths it runs about 2.4 times faster (`bc0670b`).
- After millions of rows of the first root, recounting the root by status failed: `GROUP BY` over every row
  sorts them, and SQLite's temporary files live in the connector's 64 MB `/tmp`. The counts now stream through
  a counter. Queries over the whole catalog must not sort it (duplicates use `temp_store = MEMORY` on a smaller
  set). After a failure or a pause while adding, Continue now carries on adding instead of re-running Recoll's
  check (a long pass of metadata reads on Hub).
- Re-adding after Continue ran slower than a first add: re-observing a file set its `members` column, and an
  `AFTER UPDATE OF rel_path, members` trigger fires whenever a column is named in `SET`, changed or not, so
  every unchanged file's trigram entry was deleted and rewritten. Path and members are part of the occurrence
  ID, so the update now leaves them out (`09ad510`).
- First full index, finished 2026-10-09: search holds every file plus mail messages and archive members, and
  Space then measured every root and the probable duplicates in under two minutes. Search pages answer in well
  under a second; a two-letter query, which can't use the trigram index, takes a couple of seconds.
- Space labels a folder named `temp` as temporary files because of its name, even when it is the owner's own
  staging folder. Folder-name clutter rules need a way to mark a folder as not clutter.
- ZFS counts objects, not files: `df -i` on a dataset overstates how many files it holds.
- Files that Recoll couldn't read (a small share) are mostly CHM help files with no pages, RAR archives, and
  damaged or cloud-placeholder Office files.

## Next, in order

1. **Space view** (built; see `towpath/space/` and the Space page). The reason everything is indexed is to
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

## Adding to the catalog, measured again (2026-10-17)

Hub's last full add (2026-10-09) included Recoll's reads on the spinning pool; it runs only when the owner asks
to index again. A synthetic benchmark (100,000 invented paths, Mac SSD, profiled) found two Python costs per file:

| Step | Before | After |
| --- | --- | --- |
| The containment check (`refs.locate`), about half the time | `pathlib` objects per file | text on normalised paths, through the folder cache; the original check stays for anything unusual |
| Writing the row (`store.observe`) | `dataclasses.asdict` of the whole record | the stored columns' JSON built directly |
| First add / re-adding unchanged files | 11,000 / 6,500 a second | 13,800 / 12,700 a second |

At that rate the Python side is now about as fast as Recoll's own reads, so moving it to Rust would gain at most
about twice on a step that runs only when the owner re-indexes. Not moved; measured again if re-indexing becomes
routine.
