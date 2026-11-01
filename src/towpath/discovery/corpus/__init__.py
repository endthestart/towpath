"""Generated synthetic corpus for file discovery, and the catalog an ideal provider would report.

Everything is invented and uses reserved domains. The same corpus feeds the
fixture provider (through ``catalog.json``) and the native evaluation of real
providers (which index the files themselves). Layout under ``out``:

    roots/archive/2003/old-mail.zip      ZIP > mail/backup.mbox > 3 messages; two carry paper.docx (same bytes)
    roots/archive/long/thesis-notes.txt  longer than the fixture text limit
    roots/archive/locked/secret.zip      ZipCrypto-encrypted member
    roots/archive/broken/corrupt.zip     truncated ZIP
    roots/archive/outlook/old.pst        placeholder PST (needs a helper)
    roots/archive/private/diary.txt      excluded by the example config
    roots/archive/tricky/traversal.zip   members named ../../evil.txt and /abs.txt
    roots/archive/notes/injection.md     text that tries to give instructions
    roots/archive/escape-link            symlink to outside/secret.txt (outside the root)
    roots/shared/copy-of-paper.docx      the paper's bytes again, in a second root
    outside/secret.txt                   never reachable through a root
"""

from towpath.discovery.corpus.generating import EXAMPLE_CONFIG, generate, reindex, write_example_config
from towpath.discovery.corpus.pieces import DOCX_TYPE, INJECTION_TEXT, PAPER_TEXT, TEXT_LIMIT, ZIP_DATE, docx

__all__ = [
    "DOCX_TYPE", "EXAMPLE_CONFIG", "INJECTION_TEXT", "PAPER_TEXT", "TEXT_LIMIT", "ZIP_DATE", "docx", "generate",
    "reindex", "write_example_config",
]
