# File discovery

File discovery finds documents in folders, archives, and old mail backups by asking an existing search tool, and returns references precise enough to cite and recover. Example: a college paper whose only copy is a `.docx` attachment in an mbox inside a ZIP. A useful result names the ZIP, the mailbox inside it, the message, the attachment, and where the matching passage is. Matching the ZIP's file name is not enough.

It is optional and separate from mail. It works without Gmail, model endpoints, or the life stream. Later, permitted results can become agent context or life-stream evidence. That needs separate grants, and the [context packet](#context-packet) is the only route.

Status: **design and synthetic slice**. Nothing here has run on personal data. Neither provider is adopted ([D17](decisions.md#d17-file-discovery-boundary)).

## Boundary

| Concern | Owner |
| --- | --- |
| Crawling, parsing, archive and mailbox unpacking, full-text index, OCR | The provider (an existing tool, such as Recoll or sist2), installed and run by the owner |
| Provider catalogs and text indexes | Outside Towpath, in the provider's own files |
| References, versions, coverage, citations, recoveries | `files.db`, written only by the connect role; rebuildable |
| Grants (which root may be searched, excerpted, recovered, or used as context) | The decisions store, written only by the web role; durable |
| Recovered copies | A separate `recover_dir`, never inside a searched root |

Towpath builds no crawler, parser suite, full-text engine, vector database, or backup engine.

### Dependency direction

```text
towpath.cli ── files commands (lazy import) ──> towpath.discovery.*
towpath.config ── only when [files] is present ──> towpath.discovery.config  (pure validation)
towpath.discovery.* ──> towpath.canonical, credentials, stores, config
```

Mail sync, scans, adapters, quota pacing, and the model gateway never import `towpath.discovery`, and it imports none of them. Tests enforce both directions (`tests/test_discovery_contracts.py`). Removing `[files]`, or setting `enabled = false`, leaves email exactly as it was. File records never use mail fields, and `connect.sync` is not involved.

### Modules

| Module | Responsibility |
| --- | --- |
| `discovery/config.py` | Strict `[files]` validation. Touches no files, imports no provider, starts no process |
| `discovery/records.py` | Locator, occurrence, extraction, dates, hashes, coverage |
| `discovery/refs.py` | Path safety: roots, exclusions, traversal, symlink escape, URL schemes |
| `discovery/policy.py` | Fail-closed grants per root and feature |
| `discovery/store.py` | `files.db`: runs, coverage, occurrences, versions, citations, recoveries |
| `discovery/service.py` | Probe, status, bounded search, describe, excerpt, recover, import |
| `discovery/providers/` | `base` contract; `fixture` (test aid); `recoll`; `sist2` (capability slot) |
| `discovery/evaluate.py` | Native evaluation harness over a generated corpus |
| `discovery/context.py` | Versioned, bounded context packet |
| `discovery/corpus.py` | Generated synthetic corpus and its expected catalog |

## Configuration

```toml
[files]
enabled = true
recover_dir = "state/recovered"   # must not be inside, or contain, any root

[files.limits]                    # defaults shown; each has a hard ceiling
max_results = 20                  # results per page (ceiling 200)
max_scan = 500                    # provider rows examined per request (ceiling 5000)
max_excerpt_bytes = 2000          # bytes of text per excerpt (ceiling 65536)
max_recover_bytes = 50000000      # size of one recovered copy (ceiling 2 GB)
timeout_seconds = 30              # per provider call (ceiling 600)
max_output_bytes = 4000000        # provider output read per call (ceiling 64 MB)

[[files.roots]]
alias = "old-backups"
path = "/srv/archive/old-backups"
exclude = ["private/**"]          # relative globs; matches are never shown

[[files.providers]]
id = "recoll"
adapter = "recoll"                # fixture | recoll | sist2
roots = ["old-backups"]
confdir = "/srv/recoll/old-backups"
python = "/usr/bin/python3"       # interpreter that has Recoll's Python binding
```

Unknown keys are rejected. Roots may not nest. `command` (for sist2) is an argument list; there is no shell. Loading the config does not stat, list, or open anything.

## Records

Three things are kept apart ([`records.py`](../src/towpath/discovery/records.py)):

- **Content identity:** an algorithm-labelled hash (`sha256`, `sha1`, `md5`) that was actually computed. None is ever invented. A provider's change signature, such as Recoll's `sig`, is a version token, not a hash.
- **Occurrence:** where the bytes were seen, given as a root alias, a relative path, and a member chain such as `archive-member mail/backup.mbox` > `mail-message 1` > `attachment paper.docx`.
  - Identical bytes in two places are two occurrences.
  - The occurrence ID is derived from the location, so it names a place, not immutable evidence.
  - `version` records which state of that place was seen.
  - The provider's native reference (Recoll `url` + `ipath`, sist2 `path`) is kept unchanged.
- **Extraction:** status, parser and version, extracted bytes, the limit, and truncation. The status is one of `indexed`, `truncated`, `skipped`, `unsupported`, `encrypted`, `unreadable`, or `failed`. Truncation is unknown (`null`) when the provider does not say.

Dates carry their meaning, one of `file-modified`, `member-modified`, `message-date`, `document-modified`, or `indexed-at`, and their basis. A ZIP member's mtime is not the date the paper was written.

### files.db

| Table | Holds |
| --- | --- |
| `runs` | Each import or search: provider, times, termination (`complete`, `partial`, `failed`, `interrupted`), reason |
| `coverage` | What a run examined and the count per extraction status. Only a complete run can mark anything missing |
| `occurrences` | One row per occurrence: locator, media type, size, dates, hashes, extraction, current version, first and last seen, `missing_since_run` |
| `occurrence_versions` | Every version token seen per occurrence |
| `citations` | A cited location pinned to the occurrence's version at citation time |
| `recoveries` | Each recovered copy: path, SHA-256, size, version |

`files.db` is rebuildable. Deleting it loses no decision: grants live in the decisions store, and a rebuilt catalog derives the same occurrence IDs from the same locations. A citation whose occurrence has changed version, or disappeared, resolves to **stale** or **unavailable**, never to the new content.

## Grants

Grants are per root and per feature, kept in the decisions store (`file_grants`), written by the web role and logged in `decision_log`. Without a grant, the answer is no.

| Feature | Allows |
| --- | --- |
| `search` | Results (locations, metadata, statuses) from that root |
| `excerpt` | A bounded text read of one result |
| `recover` | Writing a derived copy of one result to `recover_dir` |
| `agent-context` | Including results in a context packet for an agent |
| `life-evidence` | Including results in a context packet as life-stream evidence |

A `search` grant does not grant excerpts, recovery, agent context, or life-evidence. The model gateway's grants are separate again: nothing in file discovery calls a model.

## Safety

- **Bounded:** results, rows scanned, excerpt bytes, recovered bytes, output bytes, and time per call are capped; a provider call that exceeds its time is killed.
- **Contained:**
  - A reference resolves only inside a configured root, after following symlinks, and only from an allowed URL scheme (`file`).
  - Traversal (`..`), absolute member names, and symlink escapes are refused.
  - Excluded paths are filtered before anything is counted.
- **No leakage from a broad index:**
  - Results from roots or paths without a grant are dropped before counting.
  - Provider totals, facets, and suggestions are never passed on.
  - A result page says only whether more permitted results may exist.
- **Search does not read content:** results carry no excerpt or snippet. `excerpt` is a separate, granted, bounded read.
- **Inert content:** document text is data. It is never run as a command, and nothing in it can trigger an action. There is no shell anywhere.
- **Recovery** writes a new file under `recover_dir/<occurrence>/`, with a provenance record. It never unpacks next to the originals.
- **No source writes:** nothing moves, deletes, deduplicates, or modifies a source file.

## Providers

The provider contract (`providers/base.py`) has five operations. Each one may answer "unavailable" with a reason:

- `probe`: tool and version;
- `search`: bounded, root-scoped hits with native references;
- `describe`: the record for one native reference;
- `excerpt`: bounded text;
- `recover`: native-format bytes to a given path.

### Capability matrix

Evidence, versions, and commits are in the [provider evaluation](evaluations/file-discovery-providers.md). "Verified" means checked against the installed tool or its source at the recorded version, on synthetic files only.

| Capability | Recoll 1.36.1 | sist2 4.2.3 |
| --- | --- | --- |
| License | GPL-2.0-or-later; run as a separate program, never copied | GPL-3.0; run as a separate program, never copied |
| Interface Towpath uses | Python binding (`recoll.connect`, `Query`, `Doc`, `Extractor`), in a separate interpreter | None yet (capability slot). Documented CLI: `scan`, `index --print` (NDJSON). Raw index schema documented as unstable |
| ZIP > mbox > attachment lineage | Verified: `url` + `ipath` such as `mail/backup.mbox:1:0` | Verified: `path` such as `old-mail.zip#/mail/backup.mbox#/message-0.eml#` plus `name` |
| Message date | Verified: `dmtime` is the Date header | Not seen in `index --print` output (`mtime` was the member's) |
| Text limit | No per-document byte cap observed; see evaluation | `--content-size`, default 32,768 bytes |
| Bounded excerpt | Verified: `Extractor.textextract(ipath)` | Not documented for the CLI |
| Recover nested original | Verified: `Extractor.idoctofile`, bytes identical | Not documented for the CLI (web UI serves files; unverified) |
| Root scoping in queries | Verified: `dir:` clause; Towpath also filters every result | Not evaluated |
| Hashes | None (`sig` is a change token) | `--checksums` option (not evaluated) |
| Status here | Adapter built (stage 4) | Slot: probe and capability report only |

## Context packet

`towpath files context` writes a versioned JSON packet (`towpath.files.context/1`) for a later agent or the life stream. It holds:

- references and their versions;
- permitted excerpts;
- dates with their meaning;
- extraction limits and truncation;
- uncertainty notes;
- freshness.

It calls no model, agent, or MCP server. Each item also carries the minimal life-stream evidence reference (source ID, native ID, observed time; see [life stream](life-stream.md#records)).

## Synthetic acceptance plan

The generated corpus (`discovery/corpus.py`) contains:

- A ZIP holding an mbox whose message carries the "college paper" `.docx`.
- The same attachment bytes in a second message (duplicate occurrences).
- A long document that exceeds a text limit.
- A password-protected ZIP and a corrupt ZIP.
- A placeholder `.pst` (needs a helper).
- An excluded folder.
- A symlink pointing outside the root.
- A ZIP with traversal member names.
- A document containing prompt-injection text.

| Check | Where |
| --- | --- |
| File commands work with no Gmail, model, or life feature configured; email tests still pass | `tests/test_discovery_slice.py`, full suite |
| ZIP > mbox > message > attachment lineage complete, with passage location | fixture provider tests; native results recorded separately |
| Truncation and coverage shown honestly | fixture and native harness |
| Missing helper, encrypted, corrupt, denied grant, excluded root, absent provider: honest statuses | slice tests |
| Duplicates stay distinct occurrences | slice tests |
| A stale citation cannot serve different content | slice tests |
| Malicious references and prompt injection expose nothing and cause no action | slice tests |
| Repeated imports, interrupted runs, changed records, rebuilds preserve references and grants | slice tests |
| No private data in Git or CI | `towpath fixtures check-domains`; corpus generated at test time |
