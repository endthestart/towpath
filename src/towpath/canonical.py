"""Canonical JSON and digests, so frozen records hash the same in every process."""

import hashlib
import json


def canonical_json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def short_id(prefix: str, *parts: str, length: int = 16) -> str:
    joined = "\x1f".join(parts).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(joined).hexdigest()[:length]}"
