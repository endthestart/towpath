# Hub deployment: persistent services beside storage

The first single-owner deployment runs the existing local UI on the host's loopback address,
and reaches it through SSH. It has no public login flow. Do not expose this preview through a
LAN port or public reverse proxy. The Mac remains the development machine; the storage host
runs persistent services and indexers. GitHub builds and tests images, and Arcane pulls the
verified digests. Neither Arcane nor the deployment host builds source.

## Services and private state

Start with [the Hub Compose example](../../deploy/compose.hub.example.yml). Set both image
references to the tested, published digests from the release records, not moving tags.

- `web`: core image, host networking, listening only at `127.0.0.1:8790`. Mount the state folder
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
`TOWPATH_INDEX_DIR`, and `TOWPATH_SCRATCH_DIR`. Set `TOWPATH_MAIL_SOURCE` when invoking the
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
6. Open an SSH tunnel from the Mac: `ssh -N -L 8792:127.0.0.1:8790 <storage-host>`, then browse
   `http://127.0.0.1:8792/`. Test search, collections and one explicit queued search. Ordinary
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
