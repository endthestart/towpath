"""The package's version and limits, what a restore leaves out, and its error."""



VERSION = 1
WRITER = "web"
MAX_MEMBER = 16 * 1024 * 1024  # bytes of one file in the package, uncompressed
MAX_TOTAL = 32 * 1024 * 1024
MAIL_KEYS = {"draft_notes"}  # the only account settings restored: nothing that changes mail
TABLES = ("accounts", "settings", "dismissals", "collections", "collection_items", "questions", "sender_marks",
          "thread_marks")
LEFT_OUT = ("permissions for sources and models", "the model for each task", "finding events by itself",
            "each account's dry-run switch, allowed changes, digest, labels and filing")


class RestoreError(ValueError):
    pass
