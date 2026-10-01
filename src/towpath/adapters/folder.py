"""Folder connector: the read half of a document system or photo library.

It reports what the destination already holds, with the checksum algorithm
the destination itself uses (for example SHA-256 for a document system,
base64 SHA-1 for a photo library). It never writes to the folder.
"""

import json
from pathlib import Path

ALGORITHMS = {"sha256", "sha1-base64", "md5"}


class FolderConnector:
    def __init__(self, source_id: str, kind: str, path: Path):
        self.source_id = source_id
        self.kind = kind
        self.path = Path(path)

    def _manifest(self) -> dict:
        manifest = json.loads((self.path / "manifest.json").read_text())
        if manifest["algorithm"] not in ALGORITHMS:
            raise ValueError(f"unsupported checksum algorithm {manifest['algorithm']!r}")
        return manifest

    def describe(self) -> dict:
        return {"schema": "towpath.source/0", "source_id": self.source_id, "kind": self.kind,
                "connector": "folder/0", "authority": "read", "capabilities": ["enumerate"],
                "algorithm": self._manifest()["algorithm"]}

    def probe(self) -> dict:
        return {"ok": self.path.is_dir(), "files": len(self._manifest()["files"])}

    def enumerate(self, cursor=None, **_):
        manifest = self._manifest()
        yield ("full_start", None)
        for entry in manifest["files"]:
            yield ("destination_entry", {"path": entry["path"], "algorithm": manifest["algorithm"],
                                         "checksum": entry["checksum"], "size": entry.get("size")})
        yield ("done", {"kind": "full", "cursor": None, "complete": True})
