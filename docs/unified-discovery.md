# Unified discovery

Status: **in development on a feature branch; see [the specification](specs/unified-discovery-foundation.md).** This page describes what the code in `src/towpath/unified/` and `src/towpath/adapters/imap.py` does now. The [acceptance guide](setup/unified-discovery-acceptance.md) lists exact local checks and what is still synthetic.

Unified discovery puts one source-aware search and evidence surface over the sources Towpath already reads: Gmail and IMAP indexes in the source store, and file occurrences from an existing file-search provider ([file discovery](file-discovery.md)). Each source keeps its own store, connector and provider contract. The unified layer adds shared shapes, federation and honest reporting. It does not add a crawler, a parser suite, a full-text engine or a vector database.

## Contracts

Defined in `towpath.unified.contracts`. Every record carries a `schema` field.

| Shape | Schema | Purpose |
| --- | --- | --- |
| Source status | `towpath.source-status/0` | Registry entry: type (`gmail`, `imap`, `files`, `manifest`), state, capabilities, search depths, declared scope, coverage and freshness |
| Result | `towpath.result/0` | One hit: source, reference, kind, title, version, typed dates, locator, media type, coverage, restrictions, match, optional excerpt, and the original record under `legacy` |
| Search response | `towpath.search/0` | One page per source with its own status, depth, cursor, errors and coverage, plus the combined cursor and every reason the answer is incomplete |
| Citation | `towpath.citation/0` | Reference, version, location inside it, excerpt hash and source stamp; wraps existing file citations unchanged |

### References

A reference is `<source_id>:<native>`, where `native` is the source's own identity:

- Gmail: the message ID.
- IMAP: `mailbox;UIDVALIDITY=v;UID=u`, the full identity under [RFC 9051 §2.3.1.1](https://www.rfc-editor.org/rfc/rfc9051.html#section-2.3.1.1). The mailbox is percent-encoded except for `/`. When UIDVALIDITY changes, every old reference in that mailbox stops matching. Towpath never maps old UIDs onto new ones.
- Files: the occurrence ID from the files store.
- A MIME part: `<message native>#part=<part id>`.

A reference names a place. The result's `version` says which state of that place was seen.

### Search depth

Each source page reports the depth it used:

- `catalog`: names, types, dates and other metadata only.
- `content-index`: text that a local index extracted (for example Recoll).
- `provider-search`: the source searched itself (Gmail `q`, IMAP `SEARCH`). A provider match supplies no verified passage. The contract rejects a provider-search match that claims one.

A metadata-only query such as `extension:nef` is answered at `catalog` depth and needs no text extraction.

### Coverage

The coverage states are: `discovered`, `metadata-cataloged`, `text-extracted`, `pending`, `unreadable`, `unsupported`, `excluded`, `failed` and `unknown`.

A source reports `inventory_complete` (is every item in scope at least discovered?) and `content_complete` (is every item's text extracted or indexed?) separately. Each is `true`, `false` or `null` for not established. Content can never be complete while the inventory is not.

### Completeness

A search response is `complete` only when every source in scope did all of the following:

- answered `ok`;
- confirmed it ran out of results;
- has a complete inventory;
- for keyword text, searched content rather than metadata alone.

Provider-search coverage belongs to the provider, so a keyword answer that includes provider search is never `complete`. Every failed condition is listed in `incomplete_because`. Pages carry no combined total, and ranks are not comparable across sources.

### Dates

A typed date has a meaning, a value, a precision (`instant`, `day`, `month`, `year`, `range`, `unknown`) and a basis.

- **Evidence dates** describe the thing a source holds: `message-date`, `provider-received`, `file-modified`, `member-modified`, `document-modified`, `captured`.
- **Process dates** describe a copy or a run: `indexed-at`, `observed-at`, `exported-at`, `recovered-at`, `imported-at`.

Date filters use evidence dates by default. Process dates are flagged `process_date: true` and are never life-event dates.

### Filters

`Filters.parse` accepts keyword text mixed with typed filters:

```text
extension:nef kind:file type:image/ after:2010-01-01 before:2012-12-31 source:files-nas name:aqueduct canal walk
```

`kind` is one of `mail-message`, `mail-part`, `file`, `archive-member` or `manifest-entry`. `date:` sets both bounds.

## Federation

`towpath.unified.federation.search(adapters, filters, limit, cursor)` asks each source in scope for one page.

- An adapter failure becomes that source's `unavailable`, `denied`, `not-supported` or `error` entry. The other sources' results remain.
- Unexpected errors report their type only, because messages can hold mail data.
- An unknown source named in the filters is reported as `unknown-source`, not ignored.
- An adapter that returns more than the page limit, or answers for another source, is reported as an error.
- The combined cursor encodes only the sources that returned a continuation. A follow-up page asks only those sources.

## Source adapters

`towpath.unified.sources.build_adapters(config, connect)` registers every configured mail source (Gmail and IMAP) and every files provider. A files provider's source ID is `files-<provider id>`. Adapters run in one of two modes:

| Mode | Who uses it | What it may touch | Depths |
| --- | --- | --- | --- |
| local (`connect=False`, `--local`) | the web UI, quick checks | the source and files stores, opened read-only | `catalog` |
| connect (`connect=True`) | `towpath search` (the towpath-connect role), agents | also the providers: Gmail, IMAP, the files provider | `catalog`, `content-index`, `provider-search` |

Local mode loads no credential, builds no connector and starts no provider subprocess. A test enforces this. Metadata-only queries are always answered from the catalogs.

### Gmail

- **Catalog.** The existing metadata index: subjects, senders and attachment names.
- **Provider search.** Keyword text goes to Gmail's own `messages.list` with `q` ([reference](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/list)):
  - It uses the same `gmail.readonly` scope, the same client, and the same shared limiter. A call costs 5 units, like any listing.
  - Matches already in the local index need no further reads.
  - A match not yet indexed is read once with the existing structural field mask. It is labelled `coverage: discovered` and `indexed_locally: false`.
  - `resultSizeEstimate` is reported as an estimate, never a count.
  - Gmail operators typed in the query (`from:`, `has:attachment`) pass through unchanged.

### IMAP

A read-only connector (`src/towpath/adapters/imap.py`) built on IMAPClient. The library choice and its evidence are in [the evaluation](evaluations/imap-client.md).

Configuration:

```toml
[[sources]]
id = "imap-personal"
kind = "mail-provider"
adapter = "imap"
host = "imap.example.net"
username = "me@example.net"
password = "env:TOWPATH_IMAP_PASSWORD"   # a credential reference, never the secret
# security = "tls"                       # tls (default, port 993) | starttls (143) | plain-loopback (tests only)
# mailboxes = ["INBOX", "Archive"]       # default: every selectable mailbox
```

- **Read-only.** Only CAPABILITY, LOGIN, LIST, EXAMINE, UID SEARCH, UID FETCH with `BODY.PEEK`, and LOGOUT are sent. A server that opens an EXAMINEd mailbox read-write is refused.
- **Identity.** Each message is `mailbox;UIDVALIDITY;UID`. Indexing goes through the existing sync engine: `towpath connect sync imap-personal`.
  - Every run lists UIDs (`UID SEARCH ALL`) and fetches metadata only for new UIDs, in batches of 100.
  - The cursor records each mailbox's UIDVALIDITY and highest indexed UID, checkpointed after each mailbox.
- **Reconciliation.** Indexed messages a run no longer lists are confirmed gone before they are marked absent. That covers expunged messages, a deleted or renamed mailbox, and a changed UIDVALIDITY.
  - A mailbox that LIST still shows but that fails to open stops the run without marking anything absent.
  - A dropped connection is a clean, resumable `server-stop`.
  - A rejected login is an `auth-stop`.
- **Labels.** Each message gets its mailbox name as its label. Flags such as `\Seen` are neither read into the index nor changed.
- **Provider search.** `UID SEARCH TEXT` for each word, newest first, mailbox by mailbox. The cursor pins the mailbox position and UIDVALIDITY. If the identity changes between pages, the search stops with a `stale` error.
- **Selected content.** Content requests in the queue are fetched by `towpath connect fetch` with `BODY.PEEK[part]`. Parts use IMAP numbering (`1`, `1.2`, `2`). Base64 and quoted-printable are decoded. A part over 50 MB, or one that cannot be decoded, fails alone without stopping the batch.

### Files

- **Catalog.** Occurrences in `files.db` under roots granted `search`:
  - Paths, member names, media types and evidence dates.
  - Exclusions are re-applied on every read.
  - Missing occurrences are not searched.
  - Ungranted roots are listed in `scope.ungranted_roots` and make the inventory incomplete.
- **Content index.** In connect mode, keyword text goes through `towpath.discovery.service.search`, with all of its grant, exclusion and freshness checks. Typed filters are applied after the provider's ranking. Results carry the provider's passage offset and no text.
- **Manifest providers.** These answer only at `catalog` depth. See [the manifest adapter](file-discovery.md#manifest-adapter).

### Commands

```sh
towpath search sources [--local]                 # the registry
towpath search query "canal" [--local] [--source ID]... [--limit N] [--cursor TOKEN]
towpath search describe "imap-personal:INBOX;UIDVALIDITY=7;UID=42#part=2" [--local]
towpath fixtures unified OUT [--imap-port PORT]  # a synthetic environment for trying all of this
```

Output is JSON. Errors are `{"error": {"code", "message"}}`, with exit code 2 for a bad request and 1 for a reference that is not found.

## Legacy compatibility

`towpath.unified.envelopes` wraps the original records without changing them:

- `file_result` takes either `Occurrence.to_dict()` or a files-store row.
- `mail_result` and `mail_part_result` take source-store item rows and parts.

The original record stays under `legacy`. Hashes appear only when a provider or fetch actually computed them. The file context packet `towpath.files.context/1` is unchanged.

## Try it

`towpath search demo` runs a federated search over three synthetic sources: Gmail, IMAP, and partially indexed files. It touches no account, file or store:

```sh
towpath search demo "aqueduct"
towpath search demo "extension:nef" --limit 1
towpath search demo "aqueduct" --imap-mode unavailable
```
