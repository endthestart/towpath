# Third-party software in the towpath image

Towpath's own code (`/usr/local/lib/python3.12/site-packages/towpath`) is MIT licensed: `/usr/share/licenses/towpath/LICENSE`. Everything else in this image keeps its own license, as recorded below. Towpath's MIT license does not apply to it.

| Component | Where its license is recorded |
| --- | --- |
| Debian 13 base system: about 90 unmodified packages, including the GNU C library (LGPL-2.1+), bash and coreutils (GPL-3+), OpenSSL (Apache-2.0), and others | `/usr/share/licenses/bundled/<package>/copyright`; versions in `/usr/share/licenses/bundled/packages.tsv`; license texts in `/usr/share/common-licenses/` |
| CPython 3.12, built from source by the `python` base image (PSF-2.0) | `/usr/share/licenses/python/CPython-LICENSE.txt` |
| Python distributions (pip, Typer, Click, Rich, Pygments, and others; permissive licenses) | `/usr/share/licenses/python/packages.tsv` and `/usr/share/licenses/python/<name>-<version>/` |

The Python modules that would link GNU readline, gdbm, or Berkeley DB (`readline`, `_gdbm`, `_dbm`) are removed from this image, so CPython links no library whose license extends to it. The release workflow checks this on every build.

## Corresponding source

The release workflow publishes the exact source of every distribution package in this image before it publishes the image. The source is published in the same container registry namespace, as the OCI artifact:

```text
<registry>/<owner>/towpath-sources:sha256-<config hex>
```

`<config hex>` is this image's config digest without `sha256:`. It is the image ID on Docker's classic image store (`docker image inspect --format '{{.Id}}' <image>`). On any store, `docker buildx imagetools inspect --raw <image reference>` shows it as `config.digest` of the linux/amd64 manifest.

Each artifact holds:

- every `.dsc` and the files it lists;
- the license records above;
- a `manifest.json` tying each installed package to its source;
- `SHA256SUMS`.

Each image's release record names the artifact and its digest. To download it, run `oras pull <registry>/<owner>/towpath-sources:sha256-<config hex>`, or use any OCI client. Published source artifacts are retained indefinitely, including after the matching image is retired.
