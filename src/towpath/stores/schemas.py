"""Each store's writers, tables, and the columns added since it was first released."""

from towpath.stores.schemas_core import CORE_SCHEMAS
from towpath.stores.schemas_mail import MAIL_SCHEMAS
from towpath.stores.schemas_other import OTHER_SCHEMAS

WRITERS = {
    "source": {"connect"},
    "queue": {"worker", "web"},
    "derived": {"worker"},
    "decisions": {"web"},
    "ledger": {"worker"},
    "quota": {"connect"},
    "files": {"connect"},
    "life": {"worker"},
    "connections": {"connect"},
    "mail": {"connect"},
    "mailplan": {"worker"},
    "mailact": {"act"},
    "maildecisions": {"web"},
    "mailintel": {"worker"},
    "dav": {"connect"},
    "thumbs": {"connect"},
}

# Columns and tables added after a store was first released. Applied on every
# writer open, idempotently, so existing local databases upgrade in place.
ADDED_COLUMNS = {
    "source": {
        "runs": {"termination": "TEXT", "reason": "TEXT", "items_processed": "INTEGER"},
        "coverage": {"termination": "TEXT", "reason": "TEXT"},
        "sync_state": {"full_sync_phase": "TEXT", "full_sync_page_token": "TEXT"},
    },
    "files": {
        "citations": {"source_stamp": "TEXT"},
    },
    # Whether a stored event is a changed occurrence of a repeating one, and its STATUS (CANCELLED, ...).
    "dav": {
        "events": {"override": "INTEGER", "status": "TEXT"},
    },
}

SCHEMAS = {**CORE_SCHEMAS, **MAIL_SCHEMAS, **OTHER_SCHEMAS}
