"""Accounts a person adds in the UI. Runs only in towpath-connect, the service that holds credentials.

Non-secret settings (provider, address, server, chosen folders), indexing state and progress live in the
``connections`` store, which the web service may read. Each secret lives in the connector's credentials
folder as one file, mode 0600, and is never stored elsewhere, displayed, logged or returned: it can only be
replaced or deleted. See docs/specs/connections-in-the-ui.md.

Indexing runs only after the owner starts it. The request worker runs it in bounded slices, so searches and
content requests are not held up; Pause takes effect within seconds and every stop is resumable.
"""

from towpath.connections.ai_models import (
    MODEL_DESTINATIONS,
    MODEL_TASKS,
    TASK_LABELS,
    add_model,
    add_model_name,
    remove_model_name,
    test_model,
)
from towpath.connections.base import (
    APP_PASSWORDS,
    GOOGLE_CLIENT_FILE,
    PROVIDERS,
    ROLE,
    ConnectionProblem,
    _new_id,
    _now,
    _update,
    _write_secret,
    all_connections,
    disconnect,
    get,
    secret_path,
    token_path,
    write_secret_path,
)
from towpath.connections.effective import merged
from towpath.connections.exports import add_facebook, add_messages
from towpath.connections.gmail import (
    gmail_pacing,
    google_client,
    import_configured,
    save_gmail,
    save_google_client,
    set_verified_quota,
)
from towpath.connections.imap import (
    add_imap,
    choose_folders,
    remove_write_password,
    replace_password,
    save_write_password,
    test_imap,
    test_write_imap,
)
from towpath.connections.indexing import (
    SCHEDULES,
    _plain_stop,
    index_pending,
    next_check,
    pause_indexing,
    schedules_for,
    set_schedule,
    start_indexing,
    start_scheduled,
)

__all__ = [
    "APP_PASSWORDS", "ConnectionProblem", "GOOGLE_CLIENT_FILE", "MODEL_DESTINATIONS", "MODEL_TASKS", "PROVIDERS",
    "ROLE", "SCHEDULES", "TASK_LABELS", "_new_id", "_now", "_plain_stop", "_update", "_write_secret", "add_facebook",
    "add_imap", "add_messages", "add_model", "add_model_name", "all_connections", "choose_folders", "disconnect",
    "get", "gmail_pacing", "google_client", "import_configured", "index_pending", "merged", "next_check",
    "pause_indexing", "remove_model_name", "remove_write_password", "replace_password", "save_gmail",
    "save_google_client", "save_write_password", "schedules_for", "secret_path", "set_schedule", "set_verified_quota",
    "start_indexing", "start_scheduled", "test_imap", "test_model", "test_write_imap", "token_path",
    "write_secret_path",
]
