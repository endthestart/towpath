# File discovery providers: interface evidence

What was checked, against which version, and how. Everything here was run in a cloud session on **generated synthetic files only**. It says nothing about performance or behaviour on the owner's data. Neither tool is adopted ([D17](../decisions.md#d17-file-discovery-boundary)).

## Recoll

| Item | Value |
| --- | --- |
| Version | 1.36.1 with Xapian 1.4.22 (Ubuntu 24.04 packages `recollcmd`, `python3-recoll`) |
| License | GPL-2.0-or-later. Towpath runs it as a separate program and copies none of its code |
| Primary sources used | Docstrings of the installed Python module (`recoll`, `recoll.rclextract`; `python3 -m pydoc recoll.recoll`), the installed `mimeconf` and filters under `/usr/share/recoll`, and native runs |
| Not available here | The Recoll manual: the package's HTML manual was excluded by the container's `dpkg` path rules, and recoll.org was unreachable. Interfaces not covered by docstrings or native runs are marked unverified |

**Verified interfaces (docstrings plus a native run):**

- `recoll.connect(confdir=None, extra_dbs=None, writable=False) -> Db`; `Db.query() -> Query`.
- `Query.execute(query_string, stemming=1, stemlang="english", fetchtext=False)` returns the result count; `fetchone()`, `fetchmany(size)`, iteration, `close()`.
- `Doc` attributes seen on a nested attachment:
  - `url` (`file://` of the outer file) and `ipath` (`mail/backup.mbox:1:0`: ZIP member, message 1, attachment 0);
  - `mtype`, `filename`, `fbytes`, `dbytes`;
  - `fmtime` (outer file mtime) and `dmtime` (the message Date header);
  - `sig` (outer size + mtime) and `title`, `author`.
- `rclextract.Extractor(doc).textextract(ipath)` returns a doc whose `text` is `text/plain` or `text/html` (per its `mimetype`). For the `.docx` it was HTML.
- `Extractor(doc).idoctofile(ipath, mimetype, ofilename=...)` wrote the nested `.docx`; its SHA-256 matched the attachment's.
- Query language: `dir:"<path>"` restricts results to a folder, and works alone to list it; `filename:` matches the attachment's name.

**Observed details that matter to Towpath:**

- The mbox inside the ZIP is not a result row of its own; the message (`ipath` `mail/backup.mbox:1`) and the attachment are.
- The Python binding is compiled for one Python version (here 3.12, while the project venv is 3.11). Towpath therefore runs it in a separate interpreter (`python` in the provider config).
- `mimeconf` routes ZIP to `rclzip.py` and PST to `rclpst.py` (helper programs). Upstream notes that TAR handling is off by default. Missing helpers are listed in its `missing` file; the [native evaluation](file-discovery-native.md) records what happened.

## sist2

| Item | Value |
| --- | --- |
| Version | 4.2.3: source tag `v4.2.3` (commit `bf3325503879747d0ddb30d9ab7191b7bc378f34`), image `sist2app/sist2:4.2.3` (`sha256:6481bcdf7e806ece5e444a273acf967460e0a5385de5ab547285a3573e5edb19`). HEAD read at `5718640ae55d4a9a72dfb6c027b3e18886dd1c47` |
| License | GPL-3.0. Towpath copies none of its code |
| Primary sources used | `docs/USAGE.md`, `docs/scripting.md`, `README.md`, `libscan/email/email.c`, `libscan/sub_document.{c,h}` at the commit above; `--help` and native runs of the 4.2.3 image |

**Verified:**

- `scan --content-size` defaults to 32,768 bytes of extracted text per document.
- `--archive` is `skip|list|shallow|recurse`, default `recurse`; there are also `--archive-passphrase`, `--checksums`, and `--list-file`.
- `index --print` writes NDJSON documents to stdout.
- Lineage: a nested attachment's `path` was `old-mail.zip#/mail/backup.mbox#/message-0.eml#` with `name` `paper`, `extension` `docx`. Sub-documents are joined with `#/`, and nesting is capped at 16.
- Mail and mbox parsing exist in the 4.2.3 source (`libscan/email`, `libscan/pst`), although the README's format table does not list them.

**Documented cautions:**

- `docs/scripting.md`: the index's raw SQL schema "is not stable and can change at any time"; use the sist2-python wrapper. Towpath therefore does not read sist2 index files directly.
- **Image tags:** the image tag `latest` resolved to **4.2.1**, and `x64-linux` to **3.5.0**. Pin the version tag and digest, and check `--version` against it (the entrypoint is `/root/sist2`).
- **Search backends:** Elasticsearch (6.8, 7.x, 8.x) or a SQLite search index (`sqlite-index`). Towpath bundles neither. Elasticsearch 7.17 support has ended, so it is not a candidate. Treat the SQLite backend as a pilot only.

**Not verified (no adapter until they are):** a documented, stable way to fetch a bounded excerpt or recover a nested original from the command line; message dates for nested mail (`mtime` in `index --print` was the member's); behaviour of `--checksums` on nested members.

## Decision for stage 4

Recoll's binding documents search, excerpt, and nested recovery. All three were verified natively, so it gets the first adapter. sist2 gets a capability slot: probe and version only, with search reported as not implemented.
