#!/bin/sh
# Record the third-party software in an image, at build time:
#   /usr/share/licenses/bundled/packages.tsv           every installed distribution package
#                                                      (name, version, source package, source version)
#   /usr/share/licenses/bundled/<package>/copyright    each package's copyright file
#   /usr/share/licenses/python/                        Python distributions and their license files
# Every installed distribution package must have a copyright file, or the build fails.
# Usage: collect-licenses.sh <python interpreter>
set -eu
out=/usr/share/licenses/bundled
mkdir -p "$out"
dpkg-query -W -f='${Package}\t${Version}\t${source:Package}\t${source:Version}\n' | sort > "$out/packages.tsv"
missing=0
cut -f1 "$out/packages.tsv" | while read -r pkg; do
  if [ -f "/usr/share/doc/$pkg/copyright" ]; then
    mkdir -p "$out/$pkg"
    cp "/usr/share/doc/$pkg/copyright" "$out/$pkg/copyright"
  else
    echo "missing copyright file for $pkg" >&2
    echo "$pkg" >> /tmp/missing-copyright
  fi
done
if [ -s /tmp/missing-copyright ]; then missing=1; fi
rm -f /tmp/missing-copyright
"$1" /usr/local/lib/towpath/python-licenses.py /usr/share/licenses/python
exit "$missing"
