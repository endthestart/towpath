"""The read-only IMAP session: only allowed commands, on folders opened read-only, over a checked connection."""

import functools
import inspect
import re
import socket
from contextlib import contextmanager

from towpath.adapters.errors import AuthStop, RequestStop, ServerStop

HEADER_ITEM = "BODY.PEEK[HEADER.FIELDS (DATE SUBJECT FROM MESSAGE-ID)]"
METADATA_ITEMS = ("RFC822.SIZE", "INTERNALDATE", "BODYSTRUCTURE", HEADER_ITEM)
ALLOWED_FETCH = re.compile(  # X-GM-* are Gmail's thread and label items (X-GM-EXT-1)
    r"^(UID|FLAGS|INTERNALDATE|RFC822\.SIZE|BODYSTRUCTURE|ENVELOPE|MODSEQ|THREADID|EMAILID|X-GM-THRID|X-GM-MSGID|"
    r"X-GM-LABELS|BODY\.PEEK\[[A-Z0-9 .()\-]*\](<\d+\.\d+>)?)$")
ALLOWED_MODIFIER = re.compile(r"^CHANGEDSINCE \d+$")
ALLOWED_ENABLE = {"CONDSTORE"}
CHANGE_RESPONSES = {b"EXISTS", b"EXPUNGE", b"FETCH", b"VANISHED"}
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
FETCH_BATCH = 100
MAX_PART_BYTES = 50_000_000


class ReadOnlyViolation(RuntimeError):
    """Towpath was about to issue, or the server granted, something other than read-only access."""


def _text(value) -> str | None:
    if value is None:
        return None
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)


class ReadOnlySession:
    """The only IMAP operations Towpath performs."""

    def __init__(self, client):
        self._client = client
        client.normalise_times = False  # keep the server's time zone instead of converting to local time

    def login(self, username: str, password: str) -> None:
        self._client.login(username, password)

    def capabilities(self) -> list[str]:
        return sorted(_text(c) for c in self._client.capabilities())

    def mailboxes(self) -> list[str]:
        """Selectable mailboxes from LIST."""
        return [name for name, _ in self.folders()]

    def folders(self) -> list[tuple[str, list[str]]]:
        """Selectable mailboxes from LIST, with their attributes (including special use such as \\Trash)."""
        out = []
        for flags, _, name in self._client.list_folders():
            names = [_text(f) for f in flags]
            if any(f.lower() in {"\\noselect", "\\nonexistent"} for f in names):
                continue
            out.append((_text(name), names))
        return out

    def examine(self, mailbox: str) -> dict:
        info = self._client.select_folder(mailbox, readonly=True)
        if b"READ-WRITE" in info:
            raise ReadOnlyViolation("the server opened an EXAMINEd mailbox read-write; refusing to continue")
        modseq = info.get(b"HIGHESTMODSEQ")
        return {"uidvalidity": int(info[b"UIDVALIDITY"]), "uidnext": info.get(b"UIDNEXT"),
                "exists": info.get(b"EXISTS"), "highestmodseq": int(modseq) if modseq is not None else None}

    def enable(self, capability: str) -> bool:
        """ENABLE an extension that changes only what the server reports (CONDSTORE adds MODSEQ to responses)."""
        if capability not in ALLOWED_ENABLE:
            raise ReadOnlyViolation(f"ENABLE {capability!r} is not used")
        if capability.encode() not in {c.upper() for c in self._client.capabilities()}:
            return False
        return capability.encode() in {c.upper() for c in self._client.enable(capability)}

    def search_message_id(self, message_id: str) -> list[int]:
        """UID SEARCH for one Message-ID header value (a value, never a search key)."""
        return sorted(int(u) for u in self._client.search(["HEADER", "Message-ID", message_id]))

    def wait_for_changes(self, timeout: float) -> bool:
        """IDLE (RFC 2177) on the examined mailbox until the server reports a change or ``timeout`` passes."""
        self._client.idle()
        try:
            responses = self._client.idle_check(timeout=timeout)
        finally:
            self._client.idle_done()
        return any(len(r) > 1 and isinstance(r[1], bytes) and r[1].upper() in CHANGE_RESPONSES for r in responses)

    def search_all(self) -> list[int]:
        return sorted(int(u) for u in self._client.search(["ALL"]))

    def search_text(self, words: list[str]) -> list[int]:
        """UID SEARCH TEXT for every word (AND). Words are always values, never search keys."""
        criteria: list = []
        for word in words:
            criteria += ["TEXT", word]
        charset = None if all(w.isascii() for w in words) else "UTF-8"
        return sorted(int(u) for u in self._client.search(criteria, charset=charset))

    def fetch(self, uids: list[int], items: tuple[str, ...], modifiers: tuple[str, ...] = ()) -> dict:
        """UID FETCH of allowed items, with ``CHANGEDSINCE`` as the only modifier."""
        for item in items:
            if not ALLOWED_FETCH.match(item):
                raise ReadOnlyViolation(f"FETCH item {item!r} could change message state; only PEEK reads are used")
        for modifier in modifiers:
            if not ALLOWED_MODIFIER.match(modifier):
                raise ReadOnlyViolation(f"FETCH modifier {modifier!r} is not used")
        if isinstance(uids, str):
            raise ReadOnlyViolation("FETCH takes a list of UIDs")
        if not uids:
            return {}
        return self._client.fetch(uids, list(items), modifiers=list(modifiers) or None)

    def logout(self) -> None:
        try:
            self._client.logout()
        except Exception:  # noqa: BLE001 - a failed goodbye changes nothing on the server
            pass


def connect_session(source, password: str | None = None) -> ReadOnlySession:
    """Open, secure and log in. The password is resolved here (or passed in, while a person is setting
    the connection up) and kept nowhere."""
    from imapclient import IMAPClient

    from towpath import credentials

    if source.security == "plain-loopback" and source.host not in LOOPBACK:
        raise RequestStop("plain IMAP is allowed only to a loopback address")
    client = IMAPClient(source.host, port=source.port, ssl=source.security == "tls",
                        timeout=source.timeout_seconds)
    try:
        if source.security == "starttls":
            client.starttls()
        session = ReadOnlySession(client)
        session.login(source.username, password if password is not None else
                      credentials.resolve(source.credential) or "")
    except BaseException:
        try:
            client.shutdown()
        except Exception:  # noqa: BLE001
            pass
        raise
    return session


@contextmanager
def _mapped_errors():
    """Map library and network failures to the sync engine's clean, recorded stops."""
    try:
        from imapclient.exceptions import IMAPClientAbortError, LoginError
    except ImportError:
        raise RequestStop("IMAP support needs its optional dependency: pip install 'towpath[imap]'") from None
    try:
        yield
    except LoginError:
        raise AuthStop("the IMAP server rejected the login; check the account and credential reference") from None
    except (IMAPClientAbortError, socket.timeout, TimeoutError, ConnectionError, OSError) as exc:
        raise ServerStop(f"IMAP server or network failure ({type(exc).__name__}); progress is saved and "
                         "the next run resumes") from None


def _guarded(fn):
    if inspect.isgeneratorfunction(fn):
        @functools.wraps(fn)
        def generator(self, *args, **kwargs):
            with _mapped_errors():
                yield from fn(self, *args, **kwargs)
        return generator

    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        with _mapped_errors():
            return fn(self, *args, **kwargs)
    return wrapper
