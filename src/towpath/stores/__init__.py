"""SQLite stores, one file per store, each with a single writing role.

| Store     | Writer  | Readers        |
| --------- | ------- | -------------- |
| source    | connect | worker, web    |
| queue     | worker, web (append only) | connect |
| derived   | worker  | web            |
| decisions | web     | worker, connect |
| ledger    | worker  | web            |
| quota     | connect | connect        |
| files     | connect | worker, web    |
| connections | connect | worker, web  |
| mail      | connect | worker, act, web |
| mailplan  | worker  | act, web       |
| mailact   | act     | worker, web    |
| maildecisions | web | act, worker    |
| mailintel | worker  | web            |

Readers open with ``mode=ro`` so the database itself refuses writes.
"""

from towpath.stores.opening import RoleError, _add_columns, open_store, require_writer, store_path
from towpath.stores.schemas import SCHEMAS, WRITERS
from towpath.stores.upgrades import UPGRADE_COMMAND, SchemaOutdated, require_current, schema_status, upgrade

__all__ = [
    "RoleError", "SCHEMAS", "SchemaOutdated", "UPGRADE_COMMAND", "WRITERS", "_add_columns", "open_store",
    "require_current", "require_writer", "schema_status", "store_path", "upgrade",
]
