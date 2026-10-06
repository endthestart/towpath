# Hub deployment: persistent services beside storage

Status (2026-10-06): deployed and verified except UI access from the Mac, which waits on one host
setting (below). Work proceeds one task at a time; there is no Friday deadline.

## Current deployment

Revision `1e808f0` (dependencies pinned by `uv.lock`), CI runs
[37480821315](https://github.com/endthestart/towpath/actions/runs/37480821315) and
[37482364421](https://github.com/endthestart/towpath/actions/runs/37482364421) (publish):

- `web`: `ghcr.io/endthestart/towpath@sha256:1d79e5ebb92e3508ce00829ecfb58b42a369332ca1da67b11c1dfc18daf070bb`
- `connect`: `ghcr.io/endthestart/towpath-recoll@sha256:00e2044577ea200a05ace7f547e0b0e008d8c35ecceb7121c50345a295db725f`

Verified before and after deployment (private evidence is kept with the operator's records):

- Both corresponding-source artifacts downloaded anonymously and verified; anonymous host pulls
  matched the tested config digests and revision; a disposable, no-network synthetic run passed
  sync, store upgrade, one worker poll and UI pages on the host.
- The handed-over stores match the Mac snapshots byte for byte (SHA-256, row counts, integrity).
  State, secrets and tokens are owned by the service identity 10001 with modes 0700/0600.
- The one queued search had already been answered, so the worker found nothing pending. After
  start and after restarting both containers the stores were byte-identical: same source ID, item
  and receipt counts, no cached content, no new runs or quota attempts, no grants or collections
  before or after, OAuth token unchanged.
- The running worker loads the verified pacing: 1,800 units/minute (30% of the verified 6,000),
  0.667-second minimum interval, 1,800,000 units/day, one shared quota store.
- The UI listens only on Hub loopback and is healthy; there is no published port.
- **Open:** the host's SSH service has TCP forwarding disabled, so the tunnel in step 6 is reset.
  Next action: the owner enables TCP port forwarding for SSH on the host, then the tunnel is re-tested.

**Single writer.** Hub now owns the stores and the Gmail quota accounting. The Mac copy of the stores
and configuration is retired read-only; the owner moves its OAuth token out of use. A Mac UI, worker or
sync must not run against the same account or stores; returning to the Mac requires stopping Hub first.

**Rollback.** There is no earlier deployed digest. The fallback is the Recoll image
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

Start with [the Hub Compose example](../../deploy/compose.hub.example.yml). Set both image
references to the tested, published digests from the release records, not moving tags.
The verified Recoll image includes the same UI and mail clients and can serve both roles:
set both image variables to that published digest when the core image is not yet qualified.
The services retain their separate commands and mounts; only the connector receives tokens.

- `web`: core image on the reverse proxy's Docker network (alias `towpath-web`), listening on port
  8790 inside the container with `--public-url` set to the proxy's https address; no published
  port. On first start its log shows the setup code for creating the account at `/setup`.
  Mount the state folder (which also holds the login hash and session key)
  and a separate configuration folder with no actual secrets. Source credential references
  may name unavailable files: the UI never resolves them. No secret or OAuth token directory is mounted.
- `connect`: Recoll image with Gmail, IMAP and UI dependencies. Runs `connect run-worker`,
  processes explicit provider-search and selected-content requests, and writes receipts in
  the existing stores. Idle polls contact no provider. Search receipts and fetch receipts
  prevent completed requests from being repeated after restart. A filesystem lock excludes
  a second worker on the same stores. Graceful stop waits for current work; clean quota/auth/
  server stops wait at least five minutes before another poll. Unexpected errors log only
  their type and stop; the container has a bounded restart policy. It never schedules sync,
  models, source mutations or NAS crawling. Current grants and scope still govern results.
- `mail-sync`: manual profile for metadata sync using existing Gmail pacing and checkpoints.
- `recoll-index`: manual profile with no network, NAS input mounted read-only, and separate
  writable index and on-disk scratch folders. Begin with a qualified small folder before
  expanding the declared scope. The ordinary deployment starts neither manual profile.

Private environment values name `TOWPATH_IMAGE`, `TOWPATH_RECOLL_IMAGE`, `TOWPATH_STATE_DIR`,
`TOWPATH_CONFIG_DIR`, `TOWPATH_WEB_CONFIG_DIR`, `TOWPATH_SECRETS_DIR`, `TOWPATH_TOKEN_DIR`, `TOWPATH_SOURCE_DIR`,
`TOWPATH_INDEX_DIR`, `TOWPATH_SCRATCH_DIR`, `TOWPATH_PUBLIC_URL` (the proxy's https address) and
`TOWPATH_PROXY_NETWORK` (the proxy's existing Docker network). Set `TOWPATH_MAIL_SOURCE` when invoking the
manual sync profile. Folders must allow the container's UID/GID 10001 access; keep secrets
and private state restricted to the owner and that service identity. Use local filesystem
storage for SQLite, not an SMB/NFS mount. App passwords and OAuth material go in private
mounted files, not public Compose, Git, logs or command arguments. Static app passwords and
OAuth client configuration are mounted read-only under `/run/secrets`. The separate
`/run/tokens` directory is writable only by the connector service identity so Gmail can
atomically save refreshed access tokens. Neither directory is mounted into the web service.

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
