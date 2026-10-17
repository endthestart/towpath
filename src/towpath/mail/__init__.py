"""Mail management: Towpath reads every message of the accounts the owner chose and changes them only through
approved, receipted actions. See docs/adrs/0002-towpath-native-mail-management.md.

- ``analysis``: the connector reads headers, flags and text into the mail store (read-only connection).
- ``watch``: IDLE on each managed inbox, so new mail is indexed and read soon after it arrives.
- ``actions``: what a mailbox change is, how it is proposed, approved and described.
- ``executor``: the action runner, the only code that holds the write connection.
- ``settings``: the owner's choices (managed accounts, which action types may change mail), in the decisions store.
"""

FEATURE = "mail-manage"  # the source grant that turns mail management on for one account
