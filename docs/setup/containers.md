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

### Licenses in the Recoll image

- **What it bundles:** unmodified Ubuntu packages. Recoll, Xapian, Poppler, Antiword, and UnRTF are GPL. lxml is BSD. libpff is LGPL.
- **How Towpath uses them:** Towpath (MIT) copies none of their code and runs Recoll's binding in a separate process.
- **Where the notices are, inside the image:**
  - `/usr/share/licenses/bundled/NOTICE.md`: summary and where to get the source;
  - `/usr/share/licenses/bundled/packages.tsv`: every installed package with its version and source package;
  - `/usr/share/licenses/bundled/<package>/copyright`: each package's copyright file;
  - `/usr/share/licenses/towpath/LICENSE`: Towpath's license.

## Providers in containers

- **Recoll:** the adapter works and is tested in the `towpath-recoll` image. An index built by the `recoll-index` service, or mounted read-only from elsewhere, can be queried. A read-only mount was tested with an index built by the same image. Indexes built by other Recoll or Xapian versions are untested.
- **sist2:** only `probe` (tool and version) is implemented. Search, excerpt, recovery, and enumeration answer "not implemented". No image includes sist2, and nothing mounts a Docker socket to reach one.
- **Both together:** configuring both is possible. Combined search across them, and a full sist2 adapter, are separate future work.
- **Adoption:** neither provider has been adopted ([D17](../decisions.md#d17-file-discovery-boundary)).

## The pipeline

Defined in [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml). All Actions are pinned to full commit SHAs.

| Job | Runs on | Permissions | Does |
| --- | --- | --- | --- |
| `test` | Every push and pull request | `contents: read` | Ruff, the fixture-domain check, and the Python tests on Python 3.11, 3.12, and 3.13 |
| `image` (core, recoll) | After `test` passes | `contents: read` | Builds the candidate image, then tests it with `tests/container` (below). On trusted refs it saves the tested image and its ID as a short-lived artifact |
| `publish` (core, recoll) | Only for trusted refs (below), after `image` passes | `contents: read`, `packages: write` | Loads the saved image, checks its ID equals the tested ID, refuses to overwrite an existing tag, pushes, confirms the registry digest holds the tested image, and records the release. It checks out no code, runs no repository code, and never rebuilds |

**What the container tests check** (`tests/container/test_image.py`):

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
- **Recoll image only:**
  - The binding, helpers, and license notices are present.
  - In-container `recollindex` works, then search, excerpt, and recovery of the nested attachment (hash-checked).
  - PDF and ODT text are found.
  - A read-only index can be queried.
  - A source changed after indexing is refused as stale.
  - `deploy/compose.example.yml` runs as shipped.

**Trusted refs** (the only ones that publish):

- a push to `main`;
- a push of a tag `v*`;
- a manual run ("Run workflow") with **publish** selected, on the branch or tag chosen in that dialog.

Pull requests and other branch pushes build and test, but never publish.

**Tags and digests:**

- Every published image gets `sha-<full commit SHA>`. A release tag `vX.Y.Z` also gets `vX.Y.Z`. There is no `latest`.
- The workflow refuses to push a tag that already exists, so a revision tag always means the image that was first tested and published for it. GHCR itself does not enforce this, so **deploy by digest**.
- Each publish run writes a release record (image, digest, tags, tested image ID, revision, run URL) to the run summary and to a `release-<target>` artifact kept for 90 days.

## Owner settings (one-time)

These are outside this repository and only the owner can change them:

1. **Publishing ref.**
   - Publishing happens when this workflow runs on `main` or a `v*` tag.
   - GitHub lists a workflow for manual runs only once its file is on the default branch (`main`). Until this branch is merged, "Run workflow" is not available for it.
2. **Actions token.** Settings → Actions → General → Workflow permissions can stay at the read-only default. The `publish` job asks for `packages: write` itself.
   - If an organization or enterprise policy forbids that, publishing fails at the push step.
3. **Package visibility.**
   - The first publish creates the packages `towpath` and `towpath-recoll` under the owner's account.
   - A new package can start out private; check its visibility on the package page. If it is private, either:
     - make them public (package page → Package settings → Change visibility), or
     - give Arcane a registry credential that can read them (a classic personal access token with only `read:packages`, stored in Arcane's registry settings).

   No Arcane credential is ever stored in GitHub.
4. **Release tags.** Optionally, add a tag ruleset so only maintainers can create `v*` tags, and branch protection on `main` requiring the `ci` checks.
5. **Architecture.** If the server is not `x86_64`/`amd64`, say so: the images are `amd64` only.

## Pull, deploy, verify, roll back

All of this is manual and local to the server. Nothing in CI reaches the server.

1. **Pick a digest.** Open the publish run for the commit you want. Copy `reference` from the release record, for example `ghcr.io/<owner>/towpath-recoll@sha256:…`. The package page on GitHub also lists digests by tag.
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

`docker build --target core -t towpath:local .` and `docker build --target recoll -t towpath-recoll:local .` produce the same images. Behind a TLS-intercepting proxy, pass its CA as a build secret: `--secret id=build_ca,src=/path/to/ca.crt`. The secret is never stored in a layer.

To run the image tests against a local build:

```sh
TOWPATH_IMAGE=towpath-recoll:local TOWPATH_IMAGE_KIND=recoll python -m pytest tests/container
```

These local images are for development. Deployments use the CI-published digests.
