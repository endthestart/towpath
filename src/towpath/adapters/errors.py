class CursorExpired(Exception):
    """The provider no longer accepts the stored incremental-sync cursor."""


class Interrupted(Exception):
    """A sync stopped partway; progress so far is kept and the next run resumes."""


class NotFound(Exception):
    pass
