"""Recoll provider: Recoll's Python binding, run through ``recoll_bridge.py`` in a separate interpreter.

The owner installs Recoll, configures and runs ``recollindex``; Towpath only
queries the index. Interfaces were verified against Recoll 1.36.1 on synthetic
files (docs/evaluations/file-discovery-providers.md).

How Recoll rows become Towpath records:

- ``native_id`` is Recoll's ``rcludi``; the version is ``sig`` (outer file size and mtime).
- Members come from ``ipath`` components (``mail/backup.mbox:1:0``). A component whose own record
  is ``message/rfc822`` is a mail message; one inside a message is an attachment; one inside a ZIP,
  TAR, or 7z record or at the first level of such a file is an archive member; anything else is
  ``embedded``. Only ZIP > mbox > message > attachment has been checked natively.
- ``fmtime`` is the outer file's mtime. ``dmtime`` is the message's Date header when the item is
  inside a message (verified), otherwise the document date Recoll records.
- Size is known only for top-level files (``fbytes``). Recoll reports no hashes, no extraction
  errors, and no truncation through the binding, so those stay empty or unknown.
- The source stamp (what the index recorded about the outer file) is ``pcbytes`` (or ``fbytes`` for
  a top-level file) as size, ``fmtime`` as mtime, and the ctime that follows the size in ``sig``.
  Observed natively on 1.36.1: ``sig`` is the outer file's size followed by its whole-second ctime,
  ``pcbytes`` the outer file's size, and all three stay unchanged until ``recollindex`` runs again,
  while the extractor reads the file as it is now. ``pcbytes`` and the ``sig`` layout are not
  documented, so a ctime is used only when ``sig`` starts with the recorded size.
"""

import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from towpath.discovery import refs
from towpath.discovery.providers.base import BaseProvider, Excerpt, Hit, LimitExceeded, Listing, Unavailable
from towpath.discovery.records import DateFact, Extraction, Member, RecordError
from towpath.discovery.run import ToolError, run

BRIDGE = Path(__file__).parent.parent / "bridges" / "recoll_bridge.py"
ARCHIVE_TYPES = {"application/zip", "application/x-zip", "application/x-tar", "application/x-gzip",
                 "application/x-7z-compressed", "application/x-bzip2", "application/x-xz"}
ARCHIVE_SUFFIXES = (".zip", ".tar", ".tgz", ".tar.gz", ".7z", ".tbz2", ".tar.bz2", ".tar.xz")
SKIP_TYPES = {"inode/directory"}
ERRORS = {"no-binding": "Recoll's Python binding is not importable by the configured interpreter",
          "missing": "not in the Recoll index (it may have been re-indexed or removed)",
          "top-level": "top-level file", "failed": "Recoll reported an error"}


def _epoch(value) -> str | None:
    try:
        seconds = int(str(value))
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _int(value) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def source_stamp(row: dict) -> dict | None:
    size = _int(row.get("pcbytes"))
    if size is None and not row.get("ipath"):
        size = _int(row.get("fbytes"))
    mtime = _int(row.get("fmtime"))
    sig = str(row.get("sig") or "")
    ctime = None
    if size is not None and sig.startswith(str(size)) and sig[len(str(size)):].isdigit():
        ctime = int(sig[len(str(size)):])
    if size is None and mtime is None:
        return None
    basis = "Recoll pcbytes/fbytes, fmtime" + (", ctime from sig" if ctime is not None else "")
    return {"size": size, "mtime": mtime, "ctime": ctime, "basis": basis}


def members_from(row: dict) -> tuple[Member, ...]:
    ipath = row.get("ipath") or ""
    if not ipath:
        return ()
    parts = ipath.split(":")
    # One type per enclosing record. Recoll shortens long IDs, and then those records can't be looked up.
    known = list(row.get("ancestors") or [])[-(len(parts) - 1):] if len(parts) > 1 else []
    types = [None] * (len(parts) - 1 - len(known)) + known + [row.get("mtype")]
    outer_is_archive = row.get("url", "").lower().endswith(ARCHIVE_SUFFIXES)
    members = []
    for n, part in enumerate(parts):
        own = types[n] if n < len(types) else None
        container = types[n - 1] if n > 0 else None
        last = n == len(parts) - 1
        index = int(part) if part.isdigit() else None
        if own == "message/rfc822" and index is not None:
            members.append(Member("mail-message", None, index))
        elif container == "message/rfc822":
            name = row.get("filename") if last else None
            members.append(Member("attachment", name, index) if name or index is not None
                           else Member("attachment", part))
        elif (n == 0 and outer_is_archive) or container in ARCHIVE_TYPES:
            members.append(Member("archive-member", part))
        else:
            members.append(Member("embedded", part if index is None else None, index))
    return tuple(members)


class Provider(BaseProvider):
    adapter = "recoll"
    capabilities = {"probe": "verified", "search": "verified", "describe": "verified", "excerpt": "verified",
                    "recover": "verified", "enumerate": "verified", "root-scoped-query": "verified",
                    "hashes": "not-provided", "extraction-status": "not-provided"}

    def __init__(self, provider_config, files_config):
        super().__init__(provider_config, files_config)
        self.confdir = str(provider_config.options["confdir"])
        self.python = provider_config.options.get("python", "python3")

    def _env(self) -> dict:
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.environ.get("HOME", "/nonexistent"),
               "LANG": os.environ.get("LANG", "C.UTF-8")}
        if os.environ.get("PYTHONPATH"):
            env["PYTHONPATH"] = os.environ["PYTHONPATH"]
        return env

    def _call(self, request: dict, timeout: float) -> dict:
        import json

        try:
            result = run([self.python, str(BRIDGE)], input_bytes=json.dumps({"confdir": self.confdir, **request})
                         .encode(), timeout=timeout, max_output=self.files.limits["max_output_bytes"], env=self._env())
        except ToolError as exc:
            raise Unavailable(f"recoll {exc.code}: {exc.detail}") from None
        try:
            data = json.loads(result.stdout)
        except ValueError:
            raise Unavailable("recoll bridge returned no readable answer") from None
        if "error" in data:
            message = ERRORS.get(data["error"], data["error"])
            raise Unavailable(f"{message} ({data.get('detail', '')})" if data["error"] == "failed" else message)
        return data

    def _hit(self, row: dict) -> Hit | None:
        if row.get("mtype") in SKIP_TYPES or not row.get("url") or not row.get("rcludi"):
            return None
        try:
            members = members_from(row)
        except RecordError:
            return None
        dates = []
        if _epoch(row.get("fmtime")):
            dates.append(DateFact("file-modified", _epoch(row["fmtime"]), "Recoll fmtime (outer file mtime)"))
        if _epoch(row.get("dmtime")):
            in_message = any(m.kind == "mail-message" for m in members)
            dates.append(DateFact("message-date" if in_message else "document-modified", _epoch(row["dmtime"]),
                                  "Recoll dmtime" + (" (enclosing message's Date header)" if in_message else "")))
        return Hit(
            native_id=row["rcludi"], url=row["url"], members=members,
            extraction=Extraction("indexed", "recoll", None, _int(row.get("dbytes")), None,
                                  "Recoll holds a record; it does not report extraction errors or truncation"),
            media_type=row.get("mtype"), size=None if members else _int(row.get("fbytes")),
            dates=tuple(dates), hashes={},
            version=f"recoll-sig={row['sig']}" if row.get("sig") else None, source_stamp=source_stamp(row))

    def probe(self, timeout: float) -> dict:
        info = {"tool": "recoll", "interpreter": os.path.basename(self.python)}
        if shutil.which("recollindex"):
            try:
                out = run(["recollindex", "-c", self.confdir, "-V"], timeout=timeout, max_output=10_000)
                text = out.stderr_tail + out.stdout.decode(errors="replace")
                if "Recoll version:" in text:
                    info["version"] = text.split("Recoll version:", 1)[1].strip().splitlines()[0]
            except ToolError:
                pass
        info["binding"] = self._call({"op": "probe"}, timeout).get("binding", False)
        return info

    def _listing(self, data: dict) -> Listing:
        """Directory and unusable rows are dropped here; exhaustion is the bridge's raw-row answer."""
        rows = data["rows"]
        exhausted = data.get("exhausted")
        hits = [hit for hit in (self._hit(row) for row in rows) if hit is not None]
        return Listing(hits, exhausted if isinstance(exhausted, bool) else None, len(rows), data.get("cursor"))

    def search(self, query: str, roots: list, max_rows: int, timeout: float) -> Listing:
        dirs = [os.path.realpath(r.path) for r in roots]
        return self._listing(self._call({"op": "search", "query": query, "dirs": dirs, "max_rows": max_rows},
                                        timeout))

    def enumerate(self, root, max_rows: int, timeout: float, offset: int = 0, cursor: str | None = None) -> Listing:
        request = {"op": "enumerate", "dir": os.path.realpath(root.path), "max_rows": max_rows, "offset": offset}
        if cursor is not None:
            request["after"] = cursor
        return self._listing(self._call(request, timeout))

    def describe(self, native_id: str, timeout: float) -> Hit:
        hit = self._hit(self._call({"op": "describe", "udi": native_id}, timeout)["row"])
        if hit is None:
            raise Unavailable("not a file Towpath can describe")
        return hit

    def excerpt(self, native_id: str, start: int, max_bytes: int, timeout: float) -> Excerpt:
        data = self._call({"op": "excerpt", "udi": native_id, "start": start, "max_chars": max_bytes}, timeout)
        return Excerpt(data["text"], data["start"], data.get("total_bytes"), None)

    def recover(self, native_id: str, dest, max_bytes: int, timeout: float) -> None:
        hit = self.describe(native_id, timeout)
        if not hit.members:
            alias, rel = refs.locate(hit.url, self.roots)
            source = refs.resolve_in_root(self.roots[alias], rel)
            if source.stat().st_size > max_bytes:
                raise LimitExceeded("item is larger than max_recover_bytes")
            shutil.copyfile(source, dest)
            return
        self._call({"op": "recover", "udi": native_id, "dest": str(dest), "max_bytes": max_bytes}, timeout)
        if not Path(dest).exists():
            raise Unavailable("Recoll wrote no file")
        if Path(dest).stat().st_size > max_bytes:
            Path(dest).unlink()
            raise LimitExceeded("item is larger than max_recover_bytes")
