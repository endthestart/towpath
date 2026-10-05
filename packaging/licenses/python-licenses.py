"""Record every Python distribution visible to this interpreter, with its license files.

Writes <out>/packages.tsv (name, version, license metadata, location) and copies each
distribution's license files to <out>/<name>-<version>/. Distributions installed by the
system package manager are listed too; their copyright files are recorded separately.
"""

import shutil
import sys
from importlib import metadata
from pathlib import Path

LICENSE_NAMES = ("LICENSE", "LICENCE", "COPYING", "NOTICE", "AUTHORS")


def license_text(dist) -> str:
    meta = dist.metadata
    expression = meta.get("License-Expression")
    if expression:
        return expression
    classifiers = [c.split(" :: ")[-1] for c in meta.get_all("Classifier") or [] if c.startswith("License ::")]
    if classifiers:
        return "; ".join(classifiers)
    return (meta.get("License") or "UNKNOWN").splitlines()[0][:120]


def main(out: Path) -> int:
    out.mkdir(parents=True, exist_ok=True)
    rows, seen = [], set()
    for dist in sorted(metadata.distributions(), key=lambda d: (d.metadata["Name"] or "").lower()):
        name, version = dist.metadata["Name"], dist.version
        if not name or (name.lower(), version) in seen:
            continue
        seen.add((name.lower(), version))
        target = out / f"{name}-{version}"
        copied = 0
        for file in dist.files or []:
            parts = Path(str(file)).parts
            if any(p.upper().startswith(LICENSE_NAMES) for p in parts[-1:]) or "licenses" in parts[:-1]:
                source = Path(dist.locate_file(file))
                if source.is_file():
                    target.mkdir(exist_ok=True)
                    shutil.copyfile(source, target / "-".join(parts[1:] if len(parts) > 1 else parts))
                    copied += 1
        rows.append(f"{name}\t{version}\t{license_text(dist)}\t{copied}\t{dist.locate_file('')}")
    cpython = Path(sys.base_prefix) / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "LICENSE.txt"
    if cpython.is_file():  # CPython built from source (python:* images), not a distribution package
        shutil.copyfile(cpython, out / "CPython-LICENSE.txt")
    (out / "packages.tsv").write_text("name\tversion\tlicense\tlicense_files\tlocation\n" + "\n".join(rows) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1])))
