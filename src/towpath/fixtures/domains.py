"""Reject email addresses outside reserved domains in fixtures and tests.

Reserved per RFC 2606 and RFC 6761: example.com, example.net, example.org,
and the .test, .example, .invalid, and .localhost top-level domains.
"""

import re
from pathlib import Path

# Bounded repetition keeps the scan linear over long runs of base64 data.
ADDRESS = re.compile(r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]{1,64}@([A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63}){1,8})")
RESERVED_SECOND_LEVEL = {"example.com", "example.net", "example.org"}
RESERVED_TLDS = {"test", "example", "invalid", "localhost"}


def is_reserved(domain: str) -> bool:
    domain = domain.lower().rstrip(".")
    if domain.rsplit(".", 1)[-1] in RESERVED_TLDS:
        return True
    return any(domain == base or domain.endswith("." + base) for base in RESERVED_SECOND_LEVEL)


def violations(text: str) -> list[str]:
    return sorted({m.group(0) for m in ADDRESS.finditer(text) if not is_reserved(m.group(1))})


def check_paths(paths: list[Path]) -> dict[str, list[str]]:
    """Return ``{file: [bad addresses]}`` for text files under ``paths``."""
    found: dict[str, list[str]] = {}
    for root in paths:
        files = [root] if root.is_file() else [p for p in root.rglob("*") if p.is_file()]
        for path in files:
            if path.suffix not in {".json", ".toml", ".py", ".md", ".txt", ".eml"}:
                continue
            bad = violations(path.read_text(errors="replace"))
            if bad:
                found[str(path)] = bad
    return found
