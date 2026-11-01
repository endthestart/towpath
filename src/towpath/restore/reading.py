"""Reading a package, within its limits."""

import io
import json
import zipfile
import zlib

from towpath.restore.common import MAX_MEMBER, MAX_TOTAL, TABLES, VERSION, RestoreError


def read(data: bytes) -> dict:
    """The restore part of an export package, checked; RestoreError when it isn't one."""
    try:
        return _read(data)
    except RestoreError:
        raise
    except (zipfile.BadZipFile, zlib.error, EOFError, RuntimeError, NotImplementedError, RecursionError,
            ValueError, OSError):  # damaged, encrypted, oddly compressed or absurdly nested
        raise RestoreError("That export is damaged and can't be read.") from None


def _read(data: bytes) -> dict:
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise RestoreError("That isn't a Towpath export (not a zip file).") from None
    with z:
        infos = {i.filename: i for i in z.infolist()}
        if "restore/version.json" not in infos:
            raise RestoreError("That zip has nothing to restore (it isn't a Towpath export, or is from before "
                               "restoring was possible).")
        total = 0

        def load(name, default):
            nonlocal total
            info = infos.get(name)
            if info is None:
                return default
            if info.file_size > MAX_MEMBER or total + info.file_size > MAX_TOTAL:
                raise RestoreError("That export is too large to restore.")
            total += info.file_size
            with z.open(info) as f:
                raw = f.read(MAX_MEMBER + 1)
            if len(raw) > MAX_MEMBER:
                raise RestoreError("That export is too large to restore.")
            try:
                return json.loads(raw)
            except (ValueError, RecursionError):
                raise RestoreError(f"{name} in that export can't be read.") from None

        version = load("restore/version.json", {})
        if not isinstance(version, dict) or version.get("version") != VERSION:
            raise RestoreError("That export is from a different version of Towpath and can't be restored here.")
        found = {name: load(f"restore/{name}.json", []) for name in TABLES}
        found["rules"] = {}
        for name in infos:
            parts = name.split("/")
            if len(parts) == 3 and parts[0] == "mail" and parts[2] == "rules.json":
                found["rules"][parts[1]] = load(name, [])
    for name, rows in found.items():
        if name != "rules" and not (isinstance(rows, list) and all(isinstance(r, dict) for r in rows)):
            raise RestoreError(f"restore/{name}.json in that export can't be read.")
    return found
