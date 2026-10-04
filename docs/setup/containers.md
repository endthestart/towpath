# Container images and releases

GitHub Actions tests Towpath, builds its container images, tests the built images, and publishes the tested images to GitHub Container Registry (GHCR). A deployment, for example Arcane on the owner's server, only **pulls** a published image by digest and runs it. Nothing is built on the server, and deployment stays manual ([D18](../decisions.md#d18-container-build-and-release-path)).

## Images

| Image | Contents | Use |
| --- | --- | --- |
| `ghcr.io/<owner>/towpath` | Towpath CLI on `python:3.12-slim`, no optional extras (no Gmail or model client libraries) | File discovery with the fixture provider, `config check`, and tooling |
| `ghcr.io/<owner>/towpath-recoll` | Towpath CLI on Ubuntu 24.04 with Recoll 1.36.1, its Python binding, and document helpers (`python3-lxml` for DOCX and ODT, `poppler-utils` for PDF, `antiword` for legacy DOC, `unrtf` for RTF, `pff-tools` for PST) | File discovery through Recoll: index, search, excerpt, recover |

Both images:

- **Run unprivileged:** as uid and gid 10001. They start no service and open no port. The entrypoint is `towpath`, and the default command prints help.
- **Do nothing on their own:** no mail sync, no indexing, and no file scan. Every action is a command you run.
- **Are built for `linux/amd64` only.** An `arm64` build would be a separate change.
- **Record their source revision** in OCI labels (`org.opencontainers.image.revision`, `.source`, `.version`, `.created`) and in `/usr/share/towpath/build-info.json`.

The core image leaves out Gmail and model libraries on purpose: file discovery does not need them. To get them, build locally with `pip install '.[gmail,models]'` in a derived image. That is not published here, and mail sync stays with the existing local setup.

### Licenses and corresponding source

Towpath's own code is MIT licensed (`/usr/share/licenses/towpath/LICENSE`). Each image also carries third-party software under its own licenses. Towpath's MIT license does not apply to those components, and each image's `/usr/share/licenses/NOTICE.md` says so.

**What the images contain** (reviewed 2026-10-04 from the built images' copyright files):

| | `towpath` (core) | `towpath-recoll` |
| --- | --- | --- |
| Distribution packages | Debian 13, 87 packages | Ubuntu 24.04, 174 packages |
| Copyleft among them | 70 name a GPL license and 37 an LGPL license in their copyright files (for example bash, coreutils, glibc, util-linux). Berkeley DB 5.3 (`libdb5.3t64`) is under the Sleepycat license | 98 name a GPL license and 57 an LGPL license (Recoll, Xapian, Poppler, Antiword, UnRTF, and the base system). `pff-tools` is LGPL |
| Software outside the distribution | CPython 3.12, built from source by the `python` image (PSF-2.0); pip and Towpath's Python dependencies (MIT, BSD, ISC) | Towpath's Python dependencies in `/opt/towpath` (MIT, BSD, ISC) |

The core image removes CPython's `readline`, `_gdbm`, and `_dbm` modules. They link GNU readline and gdbm (GPL-3) and Berkeley DB (Sleepycat), whose licenses would extend to CPython itself. The source check fails if any binary outside the distribution packages links those libraries again.

**License records inside each image:**

- `/usr/share/licenses/NOTICE.md`: summary, and how to get the corresponding source;
- `/usr/share/licenses/bundled/packages.tsv`: every distribution package with its version, source package, and source version;
- `/usr/share/licenses/bundled/<package>/copyright`: each package's copyright file;
- `/usr/share/common-licenses/`: the license texts;
- `/usr/share/licenses/python/`: Python distributions, their license files, and CPython's license (core only).

**How corresponding source is delivered** ([GPL FAQ: unchanged binaries](https://www.gnu.org/licenses/gpl-faq.en.html#UnchangedJustBinary); [GPL-2.0](https://www.gnu.org/licenses/old-licenses/gpl-2.0.en.html) §3; [GPL-3.0](https://www.gnu.org/licenses/gpl.en.html) §6):

- **Upgrade at build time.** Every distribution package is upgraded to the archive's current version, so each installed version's source is still in the archive. A version from the pinned base image can be superseded; the Ubuntu base held a superseded `audit` build until this step was added.
- **Check on every build.** `sources.py check` asks the distribution's archive for the exact source version of every installed package, and fails CI if any is missing. It also fails if a non-distribution binary links readline, gdbm, or Berkeley DB.
- **Collect on publishing builds.** `sources.py collect` downloads those exact source packages. It verifies each file against the SHA-256 in its `.dsc` and the `.dsc` itself against the installed package list. It adds the license records and writes a checksummed bundle bound to the image's config digest.
- **Publish source first.** The publish job pushes the bundle to GHCR as the OCI artifact `ghcr.io/<owner>/towpath-sources:sha256-<image config hex>`, in the same place as the images. It then confirms every blob is present, and only then pushes the image.
  - An image whose source is missing, or doesn't match it, is never published or promoted.
  - If the image package can be pulled without credentials but the source package cannot, publishing stops.
- **Records.** The release record names the source artifact and its digest. The artifact holds:
  - each `.dsc` and the files it lists;
  - `licenses.tar` (the license records above);
  - `manifest.json` (packages, sources with checksums, license inventory, linkage of non-distribution binaries);
  - `SHA256SUMS`;
  - a README.
- **Size**, measured 2026-10-04: core, 61 source packages and about 313 MB (CI's availability check); Recoll, 125 source packages and about 575 MB in 396 files, collected and verified in a local run. Blobs already in the registry are reused, so later releases upload only changed sources.

To download the source of an image you have:

1. Find its config digest. The release record gives it as `config_digest`. Otherwise run `docker buildx imagetools inspect --raw <image reference>`: it prints the manifest, or an index whose `linux/amd64` entry you inspect the same way, and the manifest's `config.digest` is the digest.
2. Pull the artifact: `oras pull ghcr.io/<owner>/towpath-sources:sha256-<config digest without "sha256:">`, or use any OCI client. The release record's `sources.reference` names the same artifact by digest.
3. Check it: `sha256sum -c SHA256SUMS`, after `tar -xf licenses.tar`.

## Providers in containers

- **Recoll:** the adapter works and is tested in the `towpath-recoll` image. An index built by the `recoll-index` service, or mounted read-only from elsewhere, can be queried. A read-only mount was tested with an index built by the same image. Indexes built by other Recoll or Xapian versions are untested.
- **sist2:** only `probe` (tool and version) is implemented. Search, excerpt, recovery, and enumeration answer "not implemented". No image includes sist2, and nothing mounts a Docker socket to reach one.
- **Both together:** configuring both is possible. Combined search across them, and a full sist2 adapter, are separate future work.
- **Adoption:** neither provider has been adopted ([D17](../decisions.md#d17-file-discovery-boundary)).

## The pipeline

Defined in [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml). All Actions are pinned to full commit SHAs. The release scripts in [`packaging/release/`](../../packaging/release/) use only the Python standard library.

| Job | Runs on | Permissions | Does |
| --- | --- | --- | --- |
| `test` | Every push and pull request | `contents: read` | Ruff, the fixture-domain check, and the Python tests on Python 3.11, 3.12, and 3.13, including the release scripts' behaviour tests |
| `image` (core, recoll) | After `test` passes | `contents: read` | Builds the candidate with ref-independent build arguments, then runs `tests/container`, including a publication rehearsal (below). Checks source availability on non-publishing builds. On trusted refs it collects and verifies the source bundle and saves image and bundle as a short-lived artifact |
| `publish` (core, recoll) | Only for trusted refs, after `image` passes | `contents: read`, `packages: write`, `actions: read` | Loads the tested image and checks its ID and config digest. Runs `publish.py`: publishes source, then the canonical image if it doesn't exist yet, then release aliases, and verifies every tag. Builds nothing |

**What the container tests check** (`tests/container/`):

- **Hardened runtime:** every container runs as uid 10001, with `--network none`, `--read-only`, `--cap-drop ALL`, and `no-new-privileges`.
- **Mounts:**
  - Synthetic sources and the config are mounted read-only.
  - State, index, recovered copies, and scratch (`/tmp`) are separate writable folders on the runner's disk, not a RAM filesystem.
- **Assertions:**
  - The image is a CLI, not a service.
  - Its recorded revision matches the commit.
  - Network and writes outside the writable mounts are refused.
  - File discovery works without Gmail or model libraries.
  - Fixture search, excerpt, and recovery work, and the sources are unchanged afterwards.
  - License records cover every installed package.
  - Core only: CPython links no source-obliging library.
- **Recoll image only:**
  - The binding and helpers are present.
  - In-container `recollindex` works, then search, excerpt, and recovery of the nested attachment (hash-checked).
  - PDF and ODT text are found.
  - A read-only index can be queried.
  - A source changed after indexing is refused as stale.
  - `deploy/compose.example.yml` runs as shipped.
- **Publication rehearsal** (`test_release_rehearsal.py`), with the real `publish.py` and a real `docker push` to a throwaway registry (the official `registry` image, pinned by digest, pulled through a public mirror):
  - main, then a release tag from a different build of the same commit, then main again;
  - a conflicting release tag, an unverifiable builder run, and unreachable or refusing registries.

  Every tag is checked independently with `docker buildx imagetools`.

**Trusted refs** (the only ones that publish):

- a push to `main`;
- a push of a tag `v*`;
- a manual run ("Run workflow") with **publish** selected, on the branch or tag chosen in that dialog.

Pull requests and other branch pushes build and test, but never publish.

## Publication model

- **One canonical image per revision and target:** `ghcr.io/<owner>/towpath:sha-<commit>` and `ghcr.io/<owner>/towpath-recoll:sha-<commit>`.
  - The first trusted run to reach publication pushes its tested image there, and that tag is never rewritten.
  - The image's contents depend only on the commit: revision, package version, commit time, and repository URL. They never depend on the ref that triggered the build.
- **Release tags are aliases.** For a tag `vX.Y.Z`, the canonical manifest's exact bytes are written under `vX.Y.Z`, so the digest is the same. Nothing is rebuilt or re-pushed. The same commit's main publish and release publish therefore yield one image.
- **Promoting an existing canonical image** (a release after main, or any retry) requires all of:
  - its labels name this revision, this repository, and this image;
  - its source artifact exists, names the same image config and revision, and every blob is present;
  - the run recorded in that artifact belongs to this workflow, was for this commit, came from a trusted event, and passed `image (<target>)`, checked through the GitHub API.

  That run's own tested build is then not published (the record says so).
- **Order:** read every tag the run may change; then write source, then the image, then aliases. Then re-read every tag and require each to resolve to the canonical digest.
- **What stops a run before anything is written:**
  - a registry, authentication, or network error;
  - a release tag that already points elsewhere;
  - a release tag that exists without its canonical image;
  - a source artifact with different content under the same name.
- **Retries are safe:**
  - **Aliases written partially:** the next run verifies the existing canonical image and adds the missing aliases.
  - **Image push failed after the source was published:** the leftover source artifact is harmless. The next run publishes its own tested image and its own source.
- **Records.** Each publish run writes a release record to the run summary and to a `release-<target>` artifact kept for 90 days. It holds the image, digest, tags, config digest, whether this run created the image, which run built and tested it, the source artifact's reference and digest, and source visibility. The tags and the source artifact in GHCR are the durable record.

## Owner settings and the first publish

Only the owner can do these, outside this repository:

1. **Branch protection and release tags (before publishing).** Require the `ci` checks on `main`. Add a tag ruleset so only maintainers can create `v*` tags. A tag push is a trusted publishing ref.
2. **Actions token.** Settings → Actions → General → Workflow permissions can stay at the read-only default. The `publish` job asks for `packages: write` and `actions: read` itself.
   - If an organization or enterprise policy forbids that, publishing fails at its first registry write.
3. **Source retention.** Agree that source artifacts in `towpath-sources` are kept as long as the matching images, and for at least three years after an image was last published. The images' NOTICE promises this. Never delete a source artifact while its image is published.
4. **First publish.** Merge this branch to `main`, or push a `v*` tag after merging; either triggers it. To publish from another branch, GitHub needs the workflow on `main` first, then "Run workflow" with **publish** ticked. In the run, check that:
   - both `publish` jobs succeeded and their summaries show a release record;
   - `created_by_this_run` is true for the first publish of a commit;
   - every tag is listed with one digest;
   - the source reference is present.
5. **Visibility, all three packages together.** The first publish creates `towpath`, `towpath-recoll`, and `towpath-sources` under the owner's account, and new packages can start out private.
   - Either make all three public (package page → Package settings → Change visibility), or keep all three private and give Arcane a classic token with only `read:packages`, stored in Arcane's registry settings.
   - Never make an image public while `towpath-sources` is private: the next publish refuses, and binaries would be public without their source.
   - No Arcane credential is ever stored in GitHub.
6. **Architecture.** If the server is not `x86_64`/`amd64`, say so: the images are `amd64` only.

## Pull, deploy, verify, roll back

All of this is manual and local to the server. Nothing in CI reaches the server.

1. **Pick a digest.** Open the publish run for the commit you want, and copy `reference` from the release record, for example `ghcr.io/<owner>/towpath-recoll@sha256:…`. The package page lists digests by tag, and a release tag has the same digest as its `sha-<commit>` tag. Note the record's `sources.reference` alongside the digest.
2. **Prepare a private folder** on the server, outside this repository:
   - copy [`deploy/compose.example.yml`](../../deploy/compose.example.yml) as `compose.yml`;
   - copy [`deploy/env.example`](../../deploy/env.example) as `.env`, and set `TOWPATH_IMAGE` to the digest reference and each folder path;
   - put [`towpath.toml.example`](../../deploy/towpath.toml.example) in the config folder as `towpath.toml`;
   - put [`recoll.conf.example`](../../deploy/recoll.conf.example) in the index folder as `recoll/recoll.conf`.

   The index, state, recovered, and scratch folders must be writable by uid 10001. The source and config folders are mounted read-only.
3. **Deploy.** In Arcane, add the stack from that folder and deploy. Arcane pulls `TOWPATH_IMAGE`; the file has no `build:`. Deploying runs one `towpath files status` and the container exits with code 0. An exited container is the expected state: nothing runs continuously.
4. **Verify:**

   ```sh
   docker image inspect "$TOWPATH_IMAGE" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}'
   docker compose run --rm towpath files probe --config /config/towpath.toml
   ```

   The revision must be the commit you chose. `probe` must show Recoll 1.36 with `binding: true`.

   Then build the index and search, both manually:

   ```sh
   docker compose --profile index run --rm recoll-index
   docker compose run --rm towpath files grant archive search --config /config/towpath.toml
   docker compose run --rm towpath files search "words" --config /config/towpath.toml
   ```

5. **Roll back.** Set `TOWPATH_IMAGE` back to the previous digest and deploy again.
   - State, grants, and the index live in the mounted folders, so they survive.
   - Keep a short private list of the digests you have deployed.
   - If a newer image's Recoll version changes, re-run `recoll-index` after rolling forward or back.

## Building locally (optional)

`docker build --target core -t towpath:local .` and `docker build --target recoll -t towpath-recoll:local .` build the same images. Offline, `--build-arg DISTRO_UPGRADE=false` skips the core image's package upgrade. Such an image fails the source check, so it can never be published. Behind a TLS-intercepting proxy, pass its CA as a build secret: `--secret id=build_ca,src=/path/to/ca.crt`. The secret is never stored in a layer.

To run the image tests against a local build:

```sh
TOWPATH_IMAGE=towpath-recoll:local TOWPATH_IMAGE_KIND=recoll python -m pytest tests/container
```

These local images are for development. Deployments use the CI-published digests.
