# File discovery: native evaluation on synthetic files

`towpath files evaluate OUT` generates the [synthetic corpus](../file-discovery.md#synthetic-acceptance-plan) in a temporary folder and indexes it with each installed tool. It then asks the same questions of each and writes `OUT/report.json`. Each tool call has a time limit, and a missing tool is reported as unavailable.

The run recorded below happened in a cloud session on **synthetic files only** (raw output: [report](file-discovery-native-report.json)). It shows which behaviours exist. It does not measure performance on real archives, and adopts neither tool.

## Setup used

| | Recoll | sist2 |
| --- | --- | --- |
| Version | 1.36.1 + Xapian 1.4.22 (Ubuntu packages) | 4.2.3 (image `sist2app/sist2:4.2.3`, run with `--network none`) |
| Configuration | `topdirs` only; every other setting at its default | defaults: `--content-size 32768`, `--archive recurse` |
| Interface exercised | `recollindex`; Python binding through `recoll_bridge.py` | `scan`; `index --print` (NDJSON) |
| Matching | Recoll's own query language (`<query> dir:"<root>"`) | the harness's substring test over extracted text and path (sist2's search needs Elasticsearch or its SQLite index; not exercised) |

## Results

| Case | Recoll | sist2 |
| --- | --- | --- |
| ZIP > mbox > message > `.docx` lineage | Both copies found: `old-mail.zip` + `ipath` `mail/backup.mbox:1:0` and `:2:0`; the duplicate in the second root also found | Both copies found: `old-mail.zip#/mail/backup.mbox#/message-0.eml#/paper.docx` and `message-1`; duplicate found |
| Message numbering | 1-based (`:1`, `:2`, `:3`) | 0-based (`message-0.eml` ...) |
| Message body inside the ZIP | Found (`mail/backup.mbox:3`) | Found (`message-2.eml`) |
| Text after 54 KB of a text file | **Found**: no per-document cap at defaults | **Not found**: content cut at 32,768 bytes, as documented |
| Encrypted ZIP member | Not indexed. Only the ZIP itself is listed, with no status saying why | Not indexed. Only the ZIP itself is listed |
| Corrupt (truncated) ZIP | Listed as a ZIP with no members | Listed, and the mbox inside was partly read (`corrupt.zip#/mail/backup.mbox`) |
| Placeholder PST | Listed; `missing` file names helper `pffexport` | Listed as `application/vnd.ms-outlook-pst`, no members |
| Members named `../../evil.txt` and `/abs.txt` | Indexed with those names in `ipath` | Indexed with those names in `path` |
| Symlink pointing outside the root | Listed as `inode/symlink`; target text not indexed | Target not indexed |
| Excluded folder (`private/`) | Indexed. Exclusion is Towpath's job | Indexed. Exclusion is Towpath's job |
| Prompt-injection note | Indexed as plain text | Indexed as plain text |
| Message date of the attachment | `dmtime` = the message's Date header | `mtime` = the ZIP member's date |
| Recover the nested `.docx` | `Extractor.idoctofile` wrote it; SHA-256 identical | No documented CLI; not evaluated |
| Bounded excerpt | `Extractor.textextract` returned the paper text (HTML, converted to text by the bridge) | No documented CLI; not evaluated |
| Index time / size on this corpus | 0.13 s / 152 KiB | 0.22 s / 172 KiB (inside Docker) |

## What this means for Towpath

- Both tools meet the defining example (a document attachment in an mbox inside a ZIP) with full lineage. Neither stops at the ZIP's file name.
- Neither reports encrypted or corrupt archives as such. Towpath therefore cannot claim "not present" from a search miss: coverage shows what was indexed, and absence is never inferred from it.
- Both index hostile member names verbatim. Towpath uses member names only as labels and sanitizes recovery file names (tested).
- Both index everything under their roots. Towpath's own root, exclusion, and grant checks are required, and are applied to every row (tested with hostile provider rows).
- Recoll's binding covers search, excerpt, and recovery, so it gets the first adapter. sist2 stays a capability slot until an excerpt and recovery interface is verified.

## Not done here (local work)

- Runs on the owner's real archives: coverage, time, memory, and index size at scale.
- Recoll helpers (`pffexport` for PST, TAR support, OCR) and their effect.
- sist2 with `--content-size` raised, `--checksums`, and its SQLite search index; the sist2 web UI's file serving.
- Peak memory inside Docker (not measured: the harness sees only the Docker client).
