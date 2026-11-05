# Roadmap

This is a dependency map, not a calendar or a claim of completed capabilities.

**Current checkpoint, 2026-11-01:** Towpath runs on the owner's home server from published images (since 2026-10-06), behind a single-account login, with accounts added in the browser. Built since the 2026-10-05 checkpoint: mail management over IMAP with an action runner that runs each action type as a dry run until the owner turns it on ([ADR 0002](adrs/0002-towpath-native-mail-management.md)); the first layers of the [life stream](life-stream.md#built-2026-10-17); contacts, calendars, photo metadata, dated documents and Facebook and Apple Messages exports as sources; plain-English search through a local model. Both images now build on one Ubuntu base. The project is at a closing point, with no new features planned. Owner acceptance testing follows the [UAT guide](plans/2026-10-uat-guide.md). The milestone order below is historical. The [vision](vision.md), [D19](decisions.md#d19-reference-first-discovery-and-phased-ownership) and [D20](decisions.md#d20-foundation-before-ai-and-agent-consumers) set the direction; the [foundation specification](specs/unified-discovery-foundation.md) and [project rediscovery pilot](specs/project-rediscovery-pilot.md) remain the discovery plans.

| Milestone | Deliverable | Depends on | Required evidence |
| --- | --- | --- | --- |
| 1. Public foundation | Architecture, [services](components.md), [interfaces](interfaces.md), [integration candidates](integrations.md), [tooling review](research/2026-10-tooling-review.md), [decisions](decisions.md) | — | Public tree and history contain no private data or infrastructure details |
| 2. Synthetic read-only slice (**built**) | [First slice](first-slice.md): Gmail-shaped fixture adapter, source index, content requests, scans with presence checks against a folder destination, delivery proposals that cannot execute, decisions store, CLI | D2 | All first-slice checks pass; no write path or network access exists |
| 2b. Model endpoint contract (**built; in use with a self-hosted gateway**) | Inference gateway, probes, fallbacks, grants, model-use filtering, against a loopback stub | 2 | Slice 1b checks pass |
| 3. Mail management through Inbox Zero (**read-only adapter built; Inbox Zero stays the interim tool for Gmail while Towpath's own mail management is built, [ADR 0002](adrs/0002-towpath-native-mail-management.md)**) | [Evaluation](evaluations/inbox-zero-plan.md) on a dedicated test mailbox, then the Compose `mail` profile (or an existing instance), setup guide for separate Google Cloud projects, every model role configured, provider adapter for statistics, rules, and links | D14, D15 | Points marked "to verify" in [mail management](mail-management.md#integration-points) are answered; evaluation notes stay private, a generic summary goes public |
| 4. Towpath's read-only Gmail connector (CLI) (**built; running on the owner's server since 2026-10-06; remaining validation pending**) | `gmail.readonly` with the person's own OAuth client; incremental, resumable index, newest first; fetch on demand | 2, D3, D14 | Private acceptance on the owner's account; public tests stay synthetic |
| 5. Attachment routing and front end (**read halves, lookups, authenticated UI and service Compose built; the action runner carries out mail changes only, deliveries not built**) | Read connectors for a document system and photo library; scans; `towpath-act` deliveries; then a minimal login and overview UI (with links into the mail-management tool) and the Compose default profile | 4, D13, verified [candidates](integrations.md) | Frozen approvals, allowlist, presence rechecks, receipts; reruns propose nothing new |
| 6. Life stream from mail (**first layers built 2026-10-17**) | Mail life grant, people and event candidates, claims with citations and EDTF dates, review, timeline, excerpt capture and optional preservation, recollections, questions with cited answers when a model is bound | 4, 2b for answers | Claims open their evidence; contradictions shown; corrections survive model changes |
| 7. More life sources (**partly built**: contacts and calendars over CardDAV/CalDAV, photo metadata and dated documents in folders, Facebook and Apple Messages exports) | Contacts, then calendars, then photo library and document system as evidence sources (references, not copies) | 6, D9 | Each source works with mail disconnected |
| F. File discovery (optional; **Recoll adapter built and indexing the owner's folders; no provider formally adopted**) | [File discovery](file-discovery.md): `towpath files` through an existing search tool (Recoll adapter, sist2 slot), `files.db`, per-root grants, recovery, context packets | D17 | [Native evaluation](evaluations/file-discovery-native.md) repeated on the owner's archives; one provider chosen |
| C. Container images and releases (**built; releases published by CI and deployed by digest since 2026-10-06**) | [Containers](setup/containers.md): `towpath` and `towpath-recoll` images tested in CI, published to GHCR from trusted refs, pull-only Compose example | D18 | A publish run's release record; a manual pull, deploy, verify, and rollback on the owner's server |
| 8. Later | Other mail providers, alternative mail-management providers or Towpath-built replacements, more sources, curated family edition, export packages | 7 | Per-source grants, audience enforced in shared editions, portable export and restore |

The historical milestone table is a dependency inventory, not the current delivery order. D20 puts cross-source discovery, collection and evidence interfaces first; mail management and AI enrichment are independent later/stretch work. The life stream must work with any single source. D19 adds a later ownership direction: selected durable copies, source independence and physical organization. Their operational workflows and implementation plans remain separate decisions.

## Owner ideas to plan for (2026-10-07)

Recorded for planning; none is scheduled yet.

- **Apple data** (owner direction: ancillary, not built into Towpath). Personal backups are wanted anyway, so
  these become documented how-tos, with small standalone tools written only where nothing suitable exists;
  Towpath then indexes the copies like any other folder. iCloud Mail works today through the IMAP connection (Apple app-specific password). Contacts and
  calendars would use CardDAV/CalDAV the same way. iCloud Photos and Drive have no supported third-party API:
  the practical route is an existing open-source tool that copies them to the NAS (for example icloudpd for
  photos, which needs "Access iCloud Data on the Web" and periodic re-sign-in), then indexing the copy as
  folders. Messages have no API at all: export them on a Mac (from its Messages database) or from a local iPhone
  backup with an existing exporter, write the export to the NAS, and import it as a source. Each needs a
  verification step before adoption.
- **Photo metadata (EXIF) search.** Built 2026-10-17: the connector reads image headers with ExifTool (read-only,
  headers only) into typed fields, and photos are searched by camera, lens, date taken and whether a place is
  recorded ([`photos`](../src/towpath/photos/)). sist2 remains a candidate for thumbnails and other media.
- **Rust for hot paths** (owner direction, 2026-10-09). Python stays for the web app, orchestration and
  integrations. Work that doesn't need Python and has to be fast should be written in Rust (or a native tool)
  where possible. This is a note, not a rewrite: move a piece when measurement shows it is a bottleneck, behind
  the boundary it already has (the bridges' JSON over stdin and stdout, or the catalog tables). Candidates
  measured on Hub:
  - Adding Recoll's records to the catalog: one core at 100% before `bc0670b`, so each
    "Index again" spends hours re-adding unchanged files. Measured again on 2026-10-17: after two Python fixes
    it keeps pace with Recoll's own reads, so it stays in Python for now.
  - Space measurement and duplicates over the whole catalog.
  - A later content-hashing pass for exact duplicates.
- **Natural-language search with local models.** Built 2026-10-17 as *Ask Search all in plain English*
  ([`search_translate.py`](../src/towpath/search_translate.py)). A question such as "all pictures taken with a given
  camera" is translated by a local model into Towpath's typed query (`kind:image camera:"..."`), which Towpath runs
  under the usual grants; the model never touches the data directly, and the result can be saved as a collection.
  Builds on structured metadata above, the model gateway and grants, and the agent context interface.
