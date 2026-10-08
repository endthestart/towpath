# Third-party software in the towpath-recoll image

Towpath's own code (`/opt/towpath/lib/python3.12/site-packages/towpath`) is MIT licensed: `/usr/share/licenses/towpath/LICENSE`. Everything else in this image keeps its own license, as recorded below. Towpath's MIT license does not apply to it.

These unmodified Ubuntu 24.04 packages are added to the base system:

| Software | Purpose | License (see the package's copyright file) |
| --- | --- | --- |
| Recoll (`recollcmd`, `python3-recoll`) | Full-text index, query, and extraction used by Towpath's Recoll adapter | GPL-2.0-or-later |
| Xapian (`libxapian30`, `python3-xapian`) | Recoll's index library; its binding lists a large index by record ID | GPL-2.0-or-later |
| lxml (`python3-lxml`) | Recoll's DOCX and ODT filters | BSD-3-Clause |
| Poppler utilities (`poppler-utils`) | PDF text extraction | GPL-2.0 or GPL-3.0 |
| Antiword (`antiword`) | Legacy Word (.doc) text extraction | GPL-2.0-or-later |
| UnRTF (`unrtf`) | RTF text extraction | GPL-3.0-or-later |
| libpff tools (`pff-tools`) | Outlook PST export (`pffexport`) | LGPL-3.0-or-later |
| file (`file`, `libmagic1t64`) | Type of files without a known extension | BSD-2-Clause |
| Mutagen (`python3-mutagen`) | Audio tags (title, artist, album) | GPL-2.0-or-later |
| ExifTool (`libimage-exiftool-perl`) | Image and media metadata | Artistic-1.0-Perl or GPL-1.0-or-later |
| rarfile (`python3-rarfile`) and libarchive (`libarchive-tools`) | RAR archives, read through `bsdtar` | ISC; BSD-2-Clause |
| Ghostscript (`ghostscript`) | PostScript to PDF for text extraction | AGPL-3.0-or-later |
| PyCHM (`python3-chm`) | Compiled HTML Help (.chm) | GPL-2.0-or-later |
| zstd, XZ Utils, bzip2 (`zstd`, `xz-utils`, `bzip2`) | Compressed single files | BSD-3-Clause or GPL-2.0; 0BSD and others; bzip2-1.0.6 |
| Python 3.12 and the rest of the base system (about 170 packages in total) | Runtime | Various, per package |

Where each license is recorded:

- `/usr/share/licenses/bundled/<package>/copyright`: each package's copyright file.
- `/usr/share/licenses/bundled/packages.tsv`: every package's version and source package.
- `/usr/share/common-licenses/`: the license texts.
- `/usr/share/licenses/python/`: the Python distributions in Towpath's environment and their license files.

Towpath does not modify, link into, or copy code from these programs. Its adapter runs Recoll's Python binding in a separate process, through a bridge script (`towpath/discovery/bridges/recoll_bridge.py`, MIT, shipped as source).

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
