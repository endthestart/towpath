# Unified discovery

Status: **in development on a feature branch; see [the specification](specs/unified-discovery-foundation.md).** This page describes what the code in `src/towpath/unified/` does now. The [acceptance guide](setup/unified-discovery-acceptance.md) lists exact local checks and what is still synthetic.

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
