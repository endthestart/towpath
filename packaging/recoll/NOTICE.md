# Third-party software in the towpath-recoll image

This image adds unmodified Ubuntu 24.04 packages to Towpath (MIT). The main ones:

| Software | Purpose | License (see the package's copyright file) |
| --- | --- | --- |
| Recoll (`recollcmd`, `python3-recoll`) | Full-text index, query, and extraction used by Towpath's Recoll adapter | GPL-2.0-or-later |
| Xapian (`libxapian30`) | Recoll's index library | GPL-2.0-or-later |
| lxml (`python3-lxml`) | Recoll's DOCX and ODT filters | BSD-3-Clause |
| Poppler utilities (`poppler-utils`) | PDF text extraction | GPL-2.0 or GPL-3.0 |
| Antiword (`antiword`) | Legacy Word (.doc) text extraction | GPL-2.0-or-later |
| UnRTF (`unrtf`) | RTF text extraction | GPL-3.0-or-later |
| libpff tools (`pff-tools`) | Outlook PST export (`pffexport`) | LGPL-3.0-or-later |
| Python 3.12 and other base packages | Runtime | Various |

Towpath does not modify, link into, or copy code from these programs. Its adapter runs Recoll's Python binding in a separate process (`towpath/discovery/bridges/recoll_bridge.py`).

- **Copyright files:** every package installed in the image has its copyright file copied to `/usr/share/licenses/bundled/<package>/copyright`, where Ubuntu ships one.
- **Versions:** `/usr/share/licenses/bundled/packages.tsv` lists each installed package's name, version, source package, and source version.
- **Source code:** the corresponding source for each package is published by Ubuntu. You can get it with `apt-get source <source package>=<source version>` on an Ubuntu 24.04 system with source repositories enabled, or from https://launchpad.net/ubuntu/+source/<source package>.
