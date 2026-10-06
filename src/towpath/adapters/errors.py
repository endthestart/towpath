class CursorExpired(Exception):
    """The provider no longer accepts the stored incremental-sync cursor."""


class Interrupted(Exception):
    """A sync stopped partway; progress so far is kept and the next run resumes."""


class NotFound(Exception):
    pass


class Unfetchable(NotFound):
    """This one part cannot be fetched (too large, undecodable); the rest of a batch continues."""

    @property
    def detail(self) -> str:
        return str(self)


class InvalidPageToken(Exception):
    """A saved listing page token is no longer accepted; listing restarts from the first page."""


class SyncStop(Exception):
    """A clean, recorded stop. Progress is kept; ``reason`` never contains URLs, tokens, or mail data.

    ``code`` is recorded as the run's termination; ``exit_code`` is what the CLI returns.
    """

    code = "stop"
    exit_code = 1
    resumable = True

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class QuotaStop(SyncStop):
    """Provider quota errors persisted through every allowed retry."""

    code, exit_code = "quota-stop", 3


class DailyBudgetStop(SyncStop):
    """Towpath's own daily budget for this Google project is used up."""

    code, exit_code = "daily-budget-stop", 3


class AuthStop(SyncStop):
    """Authorization is missing, expired, or revoked; run `towpath connect auth`."""

    code, exit_code = "auth-stop", 4


class PermissionStop(SyncStop):
    code, exit_code = "permission-stop", 5


class RequestStop(SyncStop):
    """The provider rejected a request as malformed. Not retried."""

    code, exit_code = "request-stop", 6


class ServerStop(SyncStop):
    """Server or network failures persisted through every allowed retry."""

    code, exit_code = "server-stop", 7


class LockBusy(SyncStop):
    """Another Towpath command is using the same Gmail budget."""

    code, exit_code = "lock-busy", 8
