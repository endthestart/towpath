# Container release follow-up

Reviewed cloud revision: `c7de5ae7bf637cc3bc9e7e5e1f1d8ab2b16303fe`, 2026-10-04.
The main-to-release alias design now reuses the canonical tested image, and
source collection is bound to its config digest. Neither a provider adoption
nor whole-archive indexing is implied by this release work.

## Evidence

- GitHub Actions [37213959767](https://github.com/endthestart/towpath/actions/runs/37213959767)
  passed for implementation commit `ff2b055`.
- GitHub Actions [37214301155](https://github.com/endthestart/towpath/actions/runs/37214301155)
  passed for the final cloud documentation commit `c7de5ae`.
- A clean local export of `c7de5ae` passed Ruff, the fixture-domain check, and
  **212 tests with 5 optional skips**, exit 0, measured 31.47 seconds on Python 3.14.
- Synthetic regressions reproduced the download-layout, token-expiry, and
  unknown-visibility cases below before the fixes were applied.
- With these fixes, **218 tests passed with 5 optional skips**, exit 0, measured
  32.33 seconds on Python 3.14. Ruff and the fixture-domain check also passed.
  The skips reflect unavailable native provider tools; they are not native validation.

The container and source-availability checks on the cloud revision are evidence
from GitHub CI. No image was built or deployed on a server for this review.

## Small fixes

### Downloaded sources match the checksum paths

The source artifact previously named `.dsc` and related layers as top-level
filenames. Its README and `SHA256SUMS` named them under `sources/`. An OCI client
saving files by their title annotations therefore produced a tree that failed
the documented checksum command.

The layer titles now retain `sources/`. A regression materializes every layer by
its published title, extracts `licenses.tar`, and checks every path and hash in
the downloaded `SHA256SUMS`. This exercises the download layout without a real
registry or personal data. The filename convention follows the
[ORAS file-store documentation](https://github.com/oras-project/oras-go/blob/main/docs/Targets.md).
An actual ORAS pull from GHCR remains a first-release check.

### Expired bearer tokens refresh with bounded retries

The client previously reused a cached bearer token after the registry rejected
it, so an expiry between requests or during a streamed upload ended in HTTP 401.

The client now evicts a refused cached token and obtains a replacement. Requests
allow the initial challenge and one refresh, then fail on continued refusal.
Streamed uploads rewind before retrying, and the commit request uses the token
current after PATCH. Tests rotate the mock server's token between requests and
during PATCH, verify the complete uploaded bytes, and verify continued rejection
does not loop indefinitely.

### Unknown visibility stops binary publication

The anonymous visibility probe previously converted every registry error into
`private`, including timeouts and server failures. A failed image probe could
therefore allow publication while its actual public/private state was unknown.

Registry errors now preserve their HTTP status. Explicit HTTP 401/403 responses
can establish denied anonymous access; transport and other server failures
propagate and prevent binary publication. Tests cover unknown visibility for
either package and preserve the existing public/private parity checks.

Build comments also distinguish ref-independent metadata from reproducible
image contents: package archives can change, and aliases reuse the first
published canonical image rather than relying on later builds being identical.

## Remaining first-release gates

- Follow-up integrated into the cloud working branch at `ea969d1`; merged-result
  [CI passed](https://github.com/endthestart/towpath/actions/runs/37248970575).
- Source retention accepted by the owner on 2026-10-04: published source
  artifacts are retained indefinitely, including after matching images are
  retired. Both image notices and D18 record the policy.
- The actual first publishing workflow must complete Debian source collection
  and GHCR upload/download checks. Availability checks and registry rehearsals
  do not establish those results in advance.
- When making packages public, make `towpath-sources` public and confirm a
  credential-free source download before making the binary packages public.
- Only after verifying the release records, source download/checksums, image
  digests, and revision should the local operator repeat the bounded copied-file
  pilot with a pulled image. Broad NAS scanning and provider selection remain
  separate work.
