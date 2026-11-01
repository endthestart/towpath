"""Mail pages for finding and cleaning up (milestone 4): search, space, clean-up, unsubscribe and archives.

Every change made here is the owner asking for it: archive-folder moves and unsubscribes become ordinary mail
changes (dry runs until turned on); archives to a file are requests the connector carries out, removing mail from the
server only after each copy is verified, and only as the owner asked.
"""

from towpath.web.mail_clean_views.cleaning import candidates, clean
from towpath.web.mail_clean_views.leaving import archives_page, unsubscribe_ask, unsubscribe_page
from towpath.web.mail_clean_views.pages import (
    accounts_page,
    attachments_page,
    filing_mode,
    purchases_page,
    search_ask,
    search_asked,
    search_page,
    space_page,
)

__all__ = [
    "accounts_page", "archives_page", "attachments_page", "candidates", "clean", "filing_mode", "purchases_page",
    "search_ask", "search_asked", "search_page", "space_page", "unsubscribe_ask", "unsubscribe_page",
]
