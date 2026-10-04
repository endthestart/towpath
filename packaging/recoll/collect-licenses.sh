#!/bin/sh
# Record every installed Debian/Ubuntu package (name, version, source package) and copy its
# copyright file to /usr/share/licenses/bundled/<package>/copyright. The packages named as
# arguments must have a copyright file, or the build fails.
set -eu
out=/usr/share/licenses/bundled
mkdir -p "$out"
dpkg-query -W -f='${Package}\t${Version}\t${source:Package}\t${source:Version}\n' | sort > "$out/packages.tsv"
cut -f1 "$out/packages.tsv" | while read -r pkg; do
  if [ -f "/usr/share/doc/$pkg/copyright" ]; then
    mkdir -p "$out/$pkg"
    cp "/usr/share/doc/$pkg/copyright" "$out/$pkg/copyright"
  fi
done
missing=0
for pkg in "$@"; do
  if [ ! -f "$out/$pkg/copyright" ]; then
    echo "missing copyright file for required package $pkg" >&2
    missing=1
  fi
done
exit "$missing"
