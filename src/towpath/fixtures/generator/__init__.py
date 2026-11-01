"""Seeded generator for synthetic Gmail-shaped accounts and destination folders.

Every name and address is invented and uses reserved domains (RFC 2606 and
RFC 6761). The generator, not committed sample mail, is the source of truth.
"""

from towpath.fixtures.generator.pieces import Account
from towpath.fixtures.generator.writing import advance, expire_cursor, generate

__all__ = [
    "Account", "advance", "expire_cursor", "generate",
]
