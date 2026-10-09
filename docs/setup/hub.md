# Hub deployment: persistent services beside storage

Status (2026-10-06): deployed and verified. The UI is served by the host's reverse proxy behind the
single-account login; the account is created by the owner at first visit. Work proceeds one task at
a time; there is no Friday deadline.

## Current deployment

Revision `48aade4` (pages read only stored catalog counts; with `80870d2`'s faster import path check,
sort-free status counts, Continue resuming adding, and "Checking for changes" on the Folders page), publishing
run [37891163242](https://github.com/endthestart/towpath/actions/runs/37891163242) (the core image job rerun
after a registry pull failed once):

- `web`: `ghcr.io/endthestart/towpath@sha256:5d7e9e723a4dd9f5b801a9e777a0066df480941fc1312add2eafabbe3de10cbe`
- `connect` and `connect-setup`: `ghcr.io/endthestart/towpath-recoll@sha256:104af73416655c7716efd000aa31ec8bb53257ed6fe4cddd2dae370cc771de9b`

Towpath's data folder (stores, sign-ins and the Recoll index) lives on a mirrored SSD pool since 2026-10-08,
with hourly snapshots replicated to the main pool: Recoll's index writes had saturated the spinning disks. The
earlier copy on the main pool is kept for rollback. The personal datasets are mounted read-only under `/library`
in the connectors only, which join a read-only
group set in the TrueNAS GUI (Read with Inherit, applied recursively per dataset, not to child datasets). The
first folder index was running across those datasets when this release was deployed; Recoll resumed after each
restart. Measurements behind these releases are in the
[UI performance plan](../plans/2026-10-08-ui-performance-and-ux.md).

Earlier deployments, newest first (web digest, connector digest):

| Revision | Change | Images |
| --- | --- | --- |
| `80870d2` | Faster import path check; status counts without a sort; Continue resumes adding | `towpath@sha256:14c422f3…`, `towpath-recoll@sha256:7c119a5b…` |
| `fd30d80` | Each folder resolved once while adding; nested members with shortened IDs | `towpath@sha256:a975b5bf…`, `towpath-recoll@sha256:6e5f5a2c…` |
| `c615cc5` | Folders page shows files in search and the reading pace | `towpath@sha256:6074e246…`, `towpath-recoll@sha256:01eebb4d…` |
| `339f3b3` | Live panels with htmx | `towpath@sha256:7b3acc35…`, `towpath-recoll@sha256:4e19ce4a…` |
| `c8f1284` | Index batches of 512 MB by default (a setting) | `towpath@sha256:9d59c584…`, `towpath-recoll@sha256:eb418795…` |
| `e47692a` | Probable duplicates on Space; the web service later on `858ea5c` (largest files per folder) | `towpath@sha256:f3d4eeeb…` (web `9e257e1d…`), `towpath-recoll@sha256:fbb9ba8d…` |
| `97e6e24` | Adding to search beside the worker loop; Overview counts kept; plain file pages (web first, connectors after Recoll left a large archive) | `towpath@sha256:35418ebf…`, `towpath-recoll@sha256:89344115…` |
| `75a4fbb` | Indexing settings on the Folders page | `towpath@sha256:db8a9974…`, `towpath-recoll@sha256:11e49cea…` |
| `7596b0c` | Space page, fast catalog search and status, record-ID import, Recoll at two threads | `towpath@sha256:776d334f…`, `towpath-recoll@sha256:72a0211c…` |
| `2172138` | Every file indexed; unrecognised types by name only; catalog trigram index | `towpath@sha256:40fb276e…`, `towpath-recoll@sha256:1257e952…` |
| `a23c572` | Folder picker shows included subfolders; `file`, audio tags and image metadata in the Recoll image | `towpath@sha256:e81fc48f…`, `towpath-recoll@sha256:1a2a5967…` |
| `522ca49` | Folders on the server from the Connections page | `towpath@sha256:724e5780…`, `towpath-recoll@sha256:7b26805c…` |
| `6205160` | Single data folder, shell-free install, user 568; bounded source downloads | `towpath@sha256:9880ac3f…`, `towpath-recoll@sha256:b4de7395…` |
| `8f5f0c8` | Plain-language Gmail pages; separate folders, user 10001 | `towpath@sha256:293a4011…`, `towpath-recoll@sha256:fc83ae31…` |
| `c9ff8e7` | Gmail on the Connections page (configured connection imported, no Google call) | `towpath@sha256:02dbfba0…`, `towpath-recoll@sha256:b56beb7f…` |
| `0d9f36f` | Connections page for IMAP; `connect-setup` service and `/connections/` proxy route | `towpath@sha256:9617634b…`, `towpath-recoll@sha256:384d8271…` |
| `b82450f` | Search page redesign | `towpath@sha256:cc0bcfe9…`, `towpath-recoll@sha256:eee29738…` |
| `78be5ec` | Single-account login, same-origin referrer policy | `towpath@sha256:71802c79…`, `towpath-recoll@sha256:96d7e3ed…` |
| `b3f9108` | Login; do not use: its `no-referrer` policy made browser forms fail CSRF | `towpath@sha256:d43b189a…`, `towpath-recoll@sha256:5b95c639…` |
| `1e808f0` | First deployment; loopback-only UI, no login | `towpath@sha256:1d79e5eb…`, `towpath-recoll@sha256:00e20445…` |

Verified before and after deployment (private evidence is kept with the operator's records):

- For each revision, both corresponding-source artifacts downloaded anonymously and verified;
  anonymous host pulls matched the tested config digests and revision; a disposable, no-network
  synthetic run passed sync, store upgrade, one worker poll and the UI (from `b3f9108`: setup code
  logged, every page redirects to setup until the account exists). For `78be5ec`, a browser-style
  post through the proxy (real origin, no referrer) passed the CSRF check.
- The handed-over stores match the Mac snapshots byte for byte (SHA-256, row counts, integrity).
  State, secrets and tokens are owned by the service identity 10001 with modes 0700/0600.
- End to end: an owner search queued from the UI was answered by the worker in six seconds with one
  Gmail list call (5 quota units), nothing fetched or cached.
- The one queued search had already been answered, so the worker found nothing pending. After
  start, after restarting both containers, and after each redeploy the stores were byte-identical: same source ID, item
  and receipt counts, no cached content, no new runs or quota attempts, no grants or collections
  before or after, OAuth token unchanged.
- The running worker loads the verified pacing: 1,800 units/minute (30% of the verified 6,000),
  0.667-second minimum interval, 1,800,000 units/day, one shared quota store.
- The UI container is healthy, joins only the proxy's network and publishes no port. Through the
  proxy (valid TLS, from the LAN and through the public name) every page redirects to first-run
  setup; the CSRF cookie is Secure, HttpOnly and SameSite=Strict. The proxy does not restrict
  client addresses: the login is the access control. The session key is the only new file in the state folder.

**Single writer.** Hub now owns the stores and the Gmail quota accounting. The Mac copy of the stores
and configuration is retired read-only; the owner moves its OAuth token out of use. A Mac UI, worker or
sync must not run against the same account or stores; returning to the Mac requires stopping Hub first.

**Rollback.** Any release since `6205160` rolls back by putting its two digests from the table above into the
environment and redeploying; tables and indexes added later (catalog counts, Space) are ignored by older code.
To return to `8f5f0c8`: restore its compose and environment, rename `credentials/` back to
`tokens/` and give the folder back to user 10001. Before that, `c9ff8e7` by digest with the same compose as `8f5f0c8`. Before that, `0d9f36f` (same compose); the
configured Gmail entry then applies again and the imported row is ignored. Before that, `b82450f` with its compose (no `connect-setup` service, no
`/connections/` route); accounts added on the Connections page then stay stored but are not indexed. Do not roll back
to `b3f9108` (its forms fail in browsers). The last loopback-only digests are the `1e808f0` pair above. Rolling back to them
restores the loopback-only UI with no login, so restore the earlier compose (host networking, no
proxy network) and remove the proxy entry at the same time. The older fallback is the Recoll image
`ghcr.io/endthestart/towpath-recoll@sha256:2c42510606db8c2606222f40fc9295311c139019a2c52d502bf83303d93bba49`
(revision `75e361a`, fully qualified, serves both roles): check the stores read-only with its
`stores status`, set both image variables to it, then update, pull and redeploy. Hub state is covered
by hourly local ZFS snapshots kept for three days; there is no off-pool backup yet. Returning to
Mac-local operation means stopping Hub, bringing back Hub's stores with SQLite online backups if Hub
has written since the handover (otherwise the retired Mac copy), and restoring the retired
configuration and token.

The single-owner deployment serves the UI through the host's existing TLS reverse proxy (SWAG) at
its own name. The container publishes no port; the proxy terminates TLS, and every page requires
the instance's one account ([login](local-ui.md)). The Mac remains the development machine; the storage host
runs persistent services and indexers. GitHub builds and tests images, and Arcane pulls the
verified digests. Neither Arcane nor the deployment host builds source.

## Services and private state

Installation and the data-folder layout are in the [install guide](install.md), with
[the Compose example](../../deploy/compose.hub.example.yml) and [its environment](../../deploy/env.hub.example).
Images are referenced by tested, published digest, never by moving tag. All three services run as
`TOWPATH_USER` (568, TrueNAS's `apps`, on this host by the owner's choice), read-only, with no capabilities.

- `connect-setup`: the Connections pages (`/connections/`) on the proxy's network (alias
  `towpath-connect-setup`). It starts first and creates the data folder's layout. It is the only service that
  receives passwords: an account added there is tested, its password or token written to `credentials/`
  (mode 0600), and its settings to the connections store ([spec](../specs/connections-in-the-ui.md)).
- `connect`: runs `connect run-worker`. It processes explicit provider searches and selected-content requests,
  and indexes an account only after the owner presses *Start indexing* or *Check for new mail*, in bounded,
  pausable, resumable slices. Idle polls contact no provider; receipts prevent repeats after restart; a
  filesystem lock excludes a second worker; quota, auth and server stops wait at least five minutes before the
  next poll. It never schedules syncs, models, source changes or NAS crawling on its own.
- `web`: the UI on the proxy's network (alias `towpath-web`), port 8790 inside the container, no published
  port. It mounts only `state/`, so it can't read `credentials/`; it never resolves a credential. On first start
  its log shows the setup code for the single account.

Use local storage for the data folder, not an SMB or NFS mount, because SQLite needs reliable locking.
NAS indexing (Recoll) will get its own read-only source mounts and a page when that work starts.

## Move the existing index without starting over

1. Verify publication first, including corresponding-source checksums and image/revision
   identity ([container setup](containers.md)). Keep the local app working until then.
2. Stop local index writers, the request runner and UI during the final handover. Use SQLite
   online backups of every existing store. Copy snapshots and credentials through SSH into
   a private staging directory; preserve a local backup and never copy a live database
   without its transaction state. Check each snapshot's hash and SQLite integrity on Hub.
3. Rewrite only private configuration paths for container mounts. Keep source IDs, grants,
   account scope and verified Gmail budgets unchanged. Do not resolve credentials in the UI.
4. Run the explicit store upgrade on the copied stores, check schemas, and compare indexed
   item counts, cached-part counts, collection counts and queue receipts. Do not repeat a full
   Gmail scan or allow simultaneous independent quota stores for the same account.
5. Update the existing Arcane project, pull the immutable images, then deploy. Confirm each
   streamed operation completes successfully. Inspect running revision/digest, mounts,
   loopback listener, health and bounded logs. Check the original local copy is intact.
6. Install the proxy configuration (`deploy/swag-towpath.subdomain.conf.example`), open the public URL from the Mac, and create the account with the setup
   code from the web container's log. Test search, collections and one explicit queued search. Ordinary
   navigation must not cause content downloads. Fastmail credentials are configured on
   Hub afterward; qualify its read-only connector before starting full metadata indexing.

## Release upload change

The initial GHCR PATCH transfer failed with HTTP 416. Source blobs now use streamed
[OCI POST-then-PUT monolithic upload](https://github.com/opencontainers/distribution-spec/blob/main/spec.md#post-then-put),
preserving opaque upload query parameters. This removes PATCH range bookkeeping rather
than attempting speculative offset recovery. Authentication retries remain bounded and
rewind file streams; failed transfers stop publication and later runs reuse verified blobs.
Synthetic tests cover the previous failure status, empty/nonempty blobs, token expiry,
exact bytes and opaque locations. Only a successful live publishing run establishes GHCR
compatibility; synthetic passes do not close that gate. The first monolithic live attempt
hit a transport write timeout at the original 60-second limit. Blob PUTs now have a bounded
five-minute socket timeout; metadata requests retain their original timeout. Errors report
the transfer stage, digest and size without exposing upload URLs or credentials. A synthetic
delayed transfer fails with the old shared timeout and passes with the separate transfer limit.

The longer limit alone did not resolve core publication. A direct GHCR upload POST on
2026-10-06 advertised a pull-only authentication scope. The client previously cached that
token under `pull`, then looked under `pull,push` for the following PUT and started its body
without authorization. A synthetic regression reproduced this scope mismatch. The client
now requests the operation's explicit scope, authenticates the first PUT, and still permits
only one refresh when that upload token expires. This removes the reproduced missing-header
case; only a successful live core publication will establish that it resolves that upload.

The Recoll image at revision `75e361a` did publish on a bounded retry in
[run 37466516946](https://github.com/endthestart/towpath/actions/runs/37466516946).
Its complete corresponding-source artifact passed an anonymous download and checksum
verification (396 source files, 574,725,703 source bytes), including the retention notice.
Its anonymous image pull on the deployment host matched the tested config digest and
revision. A disposable, read-only, unprivileged container with no external network or
private mounts passed synthetic metadata sync, explicit store upgrade, one worker poll,
and UI/collections HTTP checks. This is host runtime qualification with invented data;
it does not establish persistent operation, private-store handover or live source acceptance.
That run's core publication failed; the scope fix above resolved it, and revisions `dd8df26` and `1e808f0` published both images.
