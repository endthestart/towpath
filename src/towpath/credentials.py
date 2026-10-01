"""Credential references.

Configuration never holds a secret, only a reference to one:
``env:NAME`` (an environment variable), ``file:PATH`` (a file, such as a
Compose secret), or ``none``. Resolved values are never logged or stored.
"""

import os
from pathlib import Path


class CredentialError(ValueError):
    pass


def validate(ref: str) -> str:
    if ref == "none" or ref.startswith("env:") or ref.startswith("file:"):
        return ref
    raise CredentialError(f"credential must be env:NAME, file:PATH, or none (got {ref.split(':')[0]!r})")


def resolve(ref: str, base: Path | None = None) -> str | None:
    validate(ref)
    if ref == "none":
        return None
    kind, _, value = ref.partition(":")
    if kind == "env":
        secret = os.environ.get(value)
        if not secret:
            raise CredentialError(f"environment variable {value} is not set")
        return secret
    path = Path(value)
    if not path.is_absolute() and base is not None:
        path = base / path
    if not path.is_file():
        raise CredentialError(f"credential file {path} does not exist")
    return path.read_text().strip()
