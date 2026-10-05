"""Corresponding source for the distribution packages in a built image.

Two modes, both run against the candidate image itself (as root, in a throwaway container with
network access to the distribution's archive):

  check    every installed package's exact source version can be fetched (``--print-uris``);
           runs on every build, so a missing source fails CI before anything is published
  collect  download those exact sources, verify them against their ``.dsc`` checksums and against
           the image's recorded package list, and assemble a bundle with the license texts

The bundle (``manifest.json``, ``SHA256SUMS``, ``sources/``, ``licenses/``) is published next to
the image before the image itself (see publish.py). Nothing here trusts a file it has not checked.

Also checks that no binary outside the distribution's packages (for example CPython in
``/usr/local``) links a library whose license obliges source for the program using it.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tarfile
from pathlib import Path

FORMAT = "towpath.sources/1"
# Libraries whose licenses extend to the programs linking them (GPL-only, or Sleepycat's
# source-availability clause). Non-distribution binaries must not link these.
SOURCE_OBLIGING_LIBRARIES = re.compile(r"/lib(?:readline|history|gdbm|gdbm_compat|db-[0-9.]+)\.so")

CONTAINER_SCRIPT = r"""
set -eu
mode="$1"; owner="$2"
mkdir -p /out/sources /out/uris /out/licenses
dpkg-query -W -f='${Package}\t${Version}\t${source:Package}\t${source:Version}\n' | sort > /out/dpkg.tsv
cp /usr/share/licenses/bundled/packages.tsv /out/image-packages.tsv
. /etc/os-release; printf '%s\n' "$PRETTY_NAME" > /out/os-release.txt
# Linkage of every ELF object that no distribution package owns.
: > /out/foreign-links.tsv
for root in /usr/local /opt; do
  [ -d "$root" ] || continue
  find "$root" -type f \( -name '*.so' -o -name '*.so.*' -o -perm -u+x \) | while read -r f; do
    if ! dpkg -S "$f" >/dev/null 2>&1; then
      ldd "$f" 2>/dev/null | awk -v f="$f" '/=> \// {print f "\t" $3}' >> /out/foreign-links.tsv || true
    fi
  done
done
for f in /etc/apt/sources.list.d/*.sources; do sed -i 's/^Types: deb$/Types: deb deb-src/' "$f"; done
if ! apt-get update -qq > /out/apt-update.log 2>&1; then cat /out/apt-update.log >&2; exit 3; fi
: > /out/failed.tsv
cut -f3,4 /out/dpkg.tsv | sort -u | while IFS="$(printf '\t')" read -r src ver; do
  if [ "$mode" = collect ]; then
    if ! (cd /out/sources && apt-get source --download-only -qq "$src=$ver" >> /out/apt-source.log 2>&1); then
      printf '%s\t%s\n' "$src" "$ver" >> /out/failed.tsv
    fi
  elif ! apt-get source --print-uris -qq "$src=$ver" > "/out/uris/${src}_${ver}.txt" 2>> /out/apt-source.log; then
    printf '%s\t%s\n' "$src" "$ver" >> /out/failed.tsv
  fi
done
cp -a /usr/share/licenses/. /out/licenses/
cp -a /usr/share/common-licenses /out/licenses/common-licenses
chown -R "$owner" /out
"""


class SourceError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_tsv(path: Path) -> list[tuple[str, ...]]:
    return [tuple(line.split("\t")) for line in path.read_text().splitlines() if line.strip()]


def strip_epoch(version: str) -> str:
    return version.split(":", 1)[1] if ":" in version else version


def parse_dsc(text: str) -> dict:
    """Fields of a Debian source control file, ignoring any OpenPGP armor around it."""
    if "-----BEGIN PGP SIGNED MESSAGE-----" in text:
        text = text.split("\n\n", 1)[1].split("-----BEGIN PGP SIGNATURE-----", 1)[0]
    fields, key = {}, None
    for line in text.splitlines():
        if not line.strip():
            continue
        if line[0] in " \t" and key:
            fields[key] += "\n" + line.strip()
        else:
            key, _, value = line.partition(":")
            key = key.strip()
            fields[key] = value.strip()
    files = []
    for entry in fields.get("Checksums-Sha256", "").splitlines():
        parts = entry.split()
        if len(parts) == 3:
            files.append({"sha256": parts[0], "size": int(parts[1]), "name": parts[2]})
    fields["files"] = files
    return fields


def parse_uris(text: str) -> list[dict]:
    """``apt-get source --print-uris`` lines: 'URI' NAME SIZE HASH."""
    rows = []
    for line in text.splitlines():
        match = re.match(r"^'([^']+)' (\S+) (\d+) (\S+)$", line.strip())
        if match:
            rows.append({"uri": match[1], "name": match[2], "size": int(match[3]), "hash": match[4]})
    return rows


def source_pairs(dpkg_rows) -> list[tuple[str, str]]:
    return sorted({(row[2], row[3]) for row in dpkg_rows})


def foreign_link_problems(work: Path) -> list[str]:
    problems = []
    for row in read_tsv(work / "foreign-links.tsv"):
        if len(row) == 2 and SOURCE_OBLIGING_LIBRARIES.search(row[1]):
            problems.append(f"{row[0]} links {row[1]}, whose license extends to programs that use it")
    return problems


def common_problems(work: Path) -> list[str]:
    problems = []
    dpkg, recorded = read_tsv(work / "dpkg.tsv"), read_tsv(work / "image-packages.tsv")
    if dpkg != recorded:
        problems.append("the image's recorded package list differs from the packages actually installed")
    for row in read_tsv(work / "failed.tsv"):
        problems.append(f"source {row[0]}={row[1]} is not available from the distribution archive")
    problems += foreign_link_problems(work)
    for package, *_ in dpkg:
        if not (work / "licenses" / "bundled" / package / "copyright").is_file():
            problems.append(f"no copyright file recorded for {package}")
    return problems


def verify_check(work: Path) -> list[str]:
    problems = common_problems(work)
    failed = {(r[0], r[1]) for r in read_tsv(work / "failed.tsv")}
    for src, ver in source_pairs(read_tsv(work / "dpkg.tsv")):
        if (src, ver) in failed:
            continue
        listing = work / "uris" / f"{src}_{ver}.txt"
        rows = parse_uris(listing.read_text()) if listing.exists() else []
        if f"{src}_{strip_epoch(ver)}.dsc" not in {r["name"] for r in rows}:
            problems.append(f"source {src}={ver}: the archive does not list {src}_{strip_epoch(ver)}.dsc")
    return problems


def verify_collected(work: Path) -> tuple[list[dict], list[str]]:
    """Check every downloaded source against its .dsc; return (sources, problems)."""
    problems = common_problems(work)
    failed = {(r[0], r[1]) for r in read_tsv(work / "failed.tsv")}
    directory = work / "sources"
    referenced, sources = set(), []
    for src, ver in source_pairs(read_tsv(work / "dpkg.tsv")):
        if (src, ver) in failed:
            continue
        dsc_name = f"{src}_{strip_epoch(ver)}.dsc"
        dsc_path = directory / dsc_name
        if not dsc_path.is_file():
            problems.append(f"source {src}={ver}: {dsc_name} was not downloaded")
            continue
        dsc = parse_dsc(dsc_path.read_text(errors="replace"))
        if dsc.get("Source") != src or dsc.get("Version") != ver:
            problems.append(f"source {src}={ver}: {dsc_name} describes {dsc.get('Source')}={dsc.get('Version')}")
            continue
        if not dsc["files"]:
            problems.append(f"source {src}={ver}: {dsc_name} lists no SHA-256 checksums")
            continue
        files = [{"name": dsc_name, "sha256": sha256_file(dsc_path), "size": dsc_path.stat().st_size}]
        referenced.add(dsc_name)
        for entry in dsc["files"]:
            path = directory / entry["name"]
            referenced.add(entry["name"])
            if not path.is_file():
                problems.append(f"source {src}={ver}: {entry['name']} is missing")
            elif path.stat().st_size != entry["size"] or sha256_file(path) != entry["sha256"]:
                problems.append(f"source {src}={ver}: {entry['name']} does not match its .dsc checksum")
            else:
                files.append(dict(entry))
        sources.append({"source": src, "version": ver, "files": files})
    for path in sorted(directory.iterdir()) if directory.is_dir() else []:
        if path.name not in referenced:
            problems.append(f"unexpected file in sources: {path.name}")
    return sources, problems


def license_inventory(work: Path) -> list[dict]:
    """Per package: the license names its machine-readable copyright file states, if any."""
    inventory = []
    for package, version, src, src_version in read_tsv(work / "dpkg.tsv"):
        path = work / "licenses" / "bundled" / package / "copyright"
        names = []
        if path.is_file():
            for line in path.read_text(errors="replace").splitlines():
                if line.startswith("License:"):
                    name = line[len("License:"):].strip()
                    if name and name not in names:
                        names.append(name)
        inventory.append({"package": package, "version": version, "source": src, "source_version": src_version,
                          "licenses": names or ["see copyright file (not machine-readable)"]})
    return inventory


def run_container(image: str, mode: str, work: Path, network: str | None) -> None:
    work.mkdir(parents=True, exist_ok=True)
    cmd = ["docker", "run", "--rm", "--user", "0", "--entrypoint", "sh", "-v", f"{work.resolve()}:/out"]
    if network:
        cmd += ["--network", network]
    cmd += [image, "-c", CONTAINER_SCRIPT, "sources", mode, f"{os.getuid()}:{os.getgid()}"]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=7200)
    if result.returncode != 0:
        raise SourceError(f"source {mode} container failed ({result.returncode}): {result.stderr[-3000:]}")


def config_digest_from_save(tar_path: Path) -> str:
    """The image config digest recorded in a ``docker save`` archive (gzip or plain)."""
    with tarfile.open(tar_path, "r:*") as archive:
        member = archive.getmember("manifest.json")
        entries = json.load(archive.extractfile(member))
    if len(entries) != 1:
        raise SourceError(f"{tar_path}: expected one image, found {len(entries)}")
    config = entries[0]["Config"]
    hexdigest = config.rsplit("/", 1)[-1].removesuffix(".json")
    if not re.fullmatch(r"[0-9a-f]{64}", hexdigest):
        raise SourceError(f"{tar_path}: unexpected config path {config!r}")
    return "sha256:" + hexdigest


def assemble(work: Path, bundle: Path, meta: dict, sources: list[dict]) -> dict:
    """Move verified sources and license texts into ``bundle`` and write its manifest and checksums."""
    inventory = license_inventory(work)  # reads work/licenses, so before it moves into the bundle
    bundle.mkdir(parents=True, exist_ok=False)
    (work / "sources").rename(bundle / "sources")
    (work / "licenses").rename(bundle / "licenses")
    readme = bundle / "README.md"
    readme.write_text(README.format(**meta))
    dpkg = read_tsv(work / "dpkg.tsv")
    manifest = {
        "format": FORMAT, **meta,
        "os": (work / "os-release.txt").read_text().strip(),
        "packages": [{"package": p, "version": v, "source": s, "source_version": sv} for p, v, s, sv in dpkg],
        "sources": sources,
        "license_inventory": inventory,
        "foreign_binary_links": [list(r) for r in read_tsv(work / "foreign-links.tsv")],
        "totals": {"packages": len(dpkg), "source_packages": len(sources),
                   "source_files": sum(len(s["files"]) for s in sources),
                   "source_bytes": sum(f["size"] for s in sources for f in s["files"])},
    }
    (bundle / "manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True))
    sums = []
    for path in sorted(p for p in bundle.rglob("*") if p.is_file() and p.name != "SHA256SUMS"):
        sums.append(f"{sha256_file(path)}  {path.relative_to(bundle).as_posix()}")
    (bundle / "SHA256SUMS").write_text("\n".join(sums) + "\n")
    return manifest


def verify_bundle(bundle: Path) -> dict:
    """Re-check a bundle before publishing it: checksums, sources against the manifest. Returns the manifest."""
    manifest = json.loads((bundle / "manifest.json").read_text())
    if manifest.get("format") != FORMAT:
        raise SourceError("unknown source bundle format")
    listed = {}
    for line in (bundle / "SHA256SUMS").read_text().splitlines():
        digest, _, name = line.partition("  ")
        listed[name] = digest
    present = {p.relative_to(bundle).as_posix() for p in bundle.rglob("*") if p.is_file() and p.name != "SHA256SUMS"}
    if set(listed) != present:
        raise SourceError("source bundle files differ from SHA256SUMS")
    for name, digest in listed.items():
        if sha256_file(bundle / name) != digest:
            raise SourceError(f"source bundle file {name} does not match SHA256SUMS")
    for source in manifest["sources"]:
        for entry in source["files"]:
            path = bundle / "sources" / entry["name"]
            if not path.is_file() or sha256_file(path) != entry["sha256"]:
                raise SourceError(f"source file {entry['name']} is missing or altered")
    wanted = {(p["source"], p["source_version"]) for p in manifest["packages"]}
    have = {(s["source"], s["version"]) for s in manifest["sources"]}
    if wanted != have:
        raise SourceError(f"source bundle lacks {sorted(wanted - have)[:5]}")
    return manifest


README = """# Corresponding source for {target} image

Image config digest: {image_config_digest}
Towpath revision: {revision}

- sources/: the exact Debian/Ubuntu source packages (.dsc and the files it lists) for every
  distribution package installed in the image, as listed in manifest.json ("packages").
- licenses/: the image's license records: each package's copyright file, the common license
  texts they refer to, Python distribution license files, and NOTICE.md.
- SHA256SUMS: checksums of every file here. Each source file also matches the SHA-256 in its .dsc.

To unpack a source package: dpkg-source -x sources/<name>.dsc

Towpath's own code is MIT licensed and published at {source_repository} (revision above). The
bundled components keep their own licenses, recorded per package in licenses/ and manifest.json.
"""


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("mode", choices=["check", "collect"])
    parser.add_argument("--image", required=True)
    parser.add_argument("--work", required=True, type=Path)
    parser.add_argument("--bundle", type=Path, help="collect: where to assemble the bundle")
    parser.add_argument("--target", default="")
    parser.add_argument("--revision", default="")
    parser.add_argument("--config-digest", default="", help="collect: config digest of the image")
    parser.add_argument("--source-repository", default="")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--run-attempt", default="")
    parser.add_argument("--network", default=None, help="docker network for the archive (default: bridge)")
    args = parser.parse_args(argv)

    run_container(args.image, args.mode, args.work, args.network)
    if args.mode == "check":
        problems = verify_check(args.work)
        pairs = source_pairs(read_tsv(args.work / "dpkg.tsv"))
        size = sum(r["size"] for p in (args.work / "uris").glob("*.txt") for r in parse_uris(p.read_text()))
        print(f"{len(pairs)} source packages, {size} bytes to collect; {len(problems)} problem(s)")
    else:
        sources, problems = verify_collected(args.work)
        if not problems:
            meta = {"target": args.target, "revision": args.revision, "image_config_digest": args.config_digest,
                    "source_repository": args.source_repository,
                    "run": {"id": args.run_id, "attempt": args.run_attempt}}
            manifest = assemble(args.work, args.bundle, meta, sources)
            verify_bundle(args.bundle)
            print(json.dumps(manifest["totals"]))
    for problem in problems:
        print(f"::error::{problem}" if os.environ.get("GITHUB_ACTIONS") else f"problem: {problem}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
