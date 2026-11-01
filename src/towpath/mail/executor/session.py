"""The write session: its own password, and only the IMAP commands an allowed change needs."""

from datetime import datetime, timezone
from pathlib import Path

from towpath.mail import actions

ROLE = "act"
LIMIT = 50  # changes per account per poll
PAUSE_SECONDS = 0.2  # between changes
EXPIRES_DAYS = 14  # a proposal approved later than this after it was made is not carried out
READ_ITEMS = ("FLAGS", "BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)]")
SYSTEM_FLAGS = {"\\Seen", "\\Flagged"}
APPEND_FLAGS = {"\\Seen", "\\Draft"}


class WriteViolation(RuntimeError):
    """Towpath was about to send a command outside the action runner's allow-list."""


class TargetChanged(Exception):
    """The message isn't where the change expects it (moved, deleted, or a different message)."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def password_path(credentials_dir: Path, source_id: str) -> Path:
    from towpath.connections import write_secret_path

    return write_secret_path(credentials_dir, source_id)


def _text(value) -> str:
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)


def _message_id(data: dict) -> str | None:
    from towpath.mail.text import MESSAGE_ID

    raw = next((v for k, v in data.items() if isinstance(k, bytes) and k.startswith(b"BODY[HEADER.FIELDS")), b"")
    found = MESSAGE_ID.findall(_text(raw or b""))
    return found[0] if found else None


class ActSession:
    """The only IMAP commands the action runner sends."""

    def __init__(self, client):
        self._client = client
        self._caps: set[str] | None = None
        self._folders: list[tuple[str, list[str]]] | None = None
        self._selected: str | None = None
        self.sent = False  # whether a changing command has been sent since this was last cleared

    def login(self, username: str, password: str) -> None:
        self._client.login(username, password)

    def capabilities(self) -> set[str]:
        if self._caps is None:
            self._caps = {_text(c).upper() for c in self._client.capabilities()}
        return self._caps

    def folders(self) -> list[tuple[str, list[str]]]:
        if self._folders is None:
            self._folders = [(_text(name), [_text(f) for f in flags])
                             for flags, _, name in self._client.list_folders()
                             if not any(_text(f).lower() in {"\\noselect", "\\nonexistent"} for f in flags)]
        return self._folders

    def special(self, use: str) -> str | None:
        """The mailbox with an RFC 6154 special use (``\\Archive``, ``\\Trash``, ``\\Drafts``), if there is one."""
        return next((name for name, flags in self.folders() if use.lower() in {f.lower() for f in flags}), None)

    def select(self, mailbox: str) -> int:
        if mailbox not in {n for n, _ in self.folders()}:
            raise TargetChanged(f"there is no mailbox named {mailbox}")
        info = self._client.select_folder(mailbox, readonly=False)
        if b"READ-WRITE" not in info:
            raise WriteViolation("the server opened the mailbox read-only; is this the write password?")
        self._selected = mailbox
        return int(info[b"UIDVALIDITY"])

    def read(self, uid: int) -> dict | None:
        """The message's flags (with its labels, on Gmail, where labels aren't flags) and Message-ID."""
        gmail = "X-GM-EXT-1" in self.capabilities()
        rows = self._client.fetch([uid], list(READ_ITEMS) + (["X-GM-LABELS"] if gmail else []))
        if uid not in rows:
            return None
        flags = {_text(f) for f in rows[uid].get(b"FLAGS", ())}
        if gmail:
            flags |= {_text(label) for label in rows[uid].get(b"X-GM-LABELS", ())}
        return {"flags": sorted(flags), "message_id": _message_id(rows[uid])}

    def find(self, message_id: str) -> list[int]:
        return sorted(int(u) for u in self._client.search(["HEADER", "Message-ID", message_id]))

    def store(self, uid: int, sign: str, flag: str) -> None:
        if flag not in SYSTEM_FLAGS and not actions.KEYWORD.match(flag):
            raise WriteViolation(f"STORE of {flag!r} is not allowed")
        self.sent = True
        if flag not in SYSTEM_FLAGS and "X-GM-EXT-1" in self.capabilities():  # Gmail: a label, not a keyword
            (self._client.add_gmail_labels if sign == "+" else self._client.remove_gmail_labels)([uid], [flag])
            return
        (self._client.add_flags if sign == "+" else self._client.remove_flags)([uid], [flag])

    def move(self, uid: int, mailbox: str) -> None:
        if "MOVE" not in self.capabilities():
            raise WriteViolation("the server lacks MOVE; Towpath doesn't copy and expunge instead")
        if mailbox not in {n for n, _ in self.folders()} or mailbox == self._selected:
            raise TargetChanged(f"can't move to {mailbox}")
        if "X-GM-EXT-1" in self.capabilities() and self._selected == self.special("\\All") and \
                mailbox != self.special("\\Trash"):
            # Gmail: taking a message out of All Mail deletes it. Putting it in a folder is adding that label.
            self.sent = True
            self._client.add_gmail_labels([uid], ["\\Inbox" if mailbox.upper() == "INBOX" else mailbox])
            return
        self.sent = True
        self._client.move([uid], mailbox)

    def append(self, role: str, raw: str, flags: tuple[str, ...]) -> str:
        mailbox = "INBOX" if role == "inbox" else self.special("\\Drafts")
        if mailbox is None:
            raise TargetChanged("the account has no Drafts mailbox")
        if set(flags) - APPEND_FLAGS:
            raise WriteViolation(f"APPEND with flags {sorted(flags)} is not allowed")
        self.sent = True
        self._client.append(mailbox, raw.encode("utf-8"), flags=flags)
        return mailbox

    def unselect(self) -> None:
        """Leave the mailbox without expunging: UNSELECT, or EXAMINE it (never CLOSE, which expunges)."""
        if self._selected is None:
            return
        if "UNSELECT" in self.capabilities():
            self._client.unselect_folder()
        else:
            self._client.select_folder(self._selected, readonly=True)
        self._selected = None

    def logout(self) -> None:
        try:
            self._client.logout()
        except Exception:  # noqa: BLE001 - a failed goodbye changes nothing
            pass


def connect_act(source, password: str) -> ActSession:
    from imapclient import IMAPClient

    from towpath.adapters.imap import LOOPBACK
    from towpath.adapters.errors import RequestStop

    if source.security == "plain-loopback" and source.host not in LOOPBACK:
        raise RequestStop("plain IMAP is allowed only to a loopback address")
    client = IMAPClient(source.host, port=source.port, ssl=source.security == "tls", timeout=source.timeout_seconds)
    try:
        if source.security == "starttls":
            client.starttls()
        session = ActSession(client)
        session.login(source.username, password)
    except BaseException:
        try:
            client.shutdown()
        except Exception:  # noqa: BLE001
            pass
        raise
    return session
