"""Email accounts reached over IMAP: testing a password, choosing folders, and the separate write password."""

import json
import socket
import ssl
from contextlib import closing
from pathlib import Path

from towpath.connections.base import (
    PROVIDERS,
    ROLE,
    ConnectionProblem,
    _new_id,
    _now,
    _require,
    _update,
    _write_secret,
    get,
    secret_path,
    write_secret_path,
)
from towpath.stores import open_store

# Folders left out by default: special use (RFC 6154) first, then common names for servers that omit it.
SKIP_SPECIAL = {"\\trash", "\\junk", "\\drafts"}
SKIP_NAMES = {"trash", "deleted items", "deleted messages", "spam", "junk", "junk mail", "junk e-mail", "drafts",
              "bulk", "bulk mail", "notes"}  # Yahoo's spam is "Bulk"; iCloud lists its Notes as a mailbox


def _imap_source(sid: str, settings: dict, credential: str):
    from towpath import config as config_mod

    raw = {"id": sid, "kind": "mail-provider", "adapter": "imap", "host": settings["host"],
           "port": settings["port"], "security": settings["security"], "username": settings["username"],
           "password": credential, "timeout_seconds": settings.get("timeout_seconds", 60)}
    if settings.get("mailboxes"):
        raw["mailboxes"] = list(settings["mailboxes"])
    try:
        return config_mod._imap_source(sid, raw, Path("/"))
    except config_mod.ConfigError as exc:
        raise ConnectionProblem(str(exc).split(": ", 1)[-1].capitalize() + ".") from None


GMAIL_DEFAULTS = {"inbox", "[gmail]/all mail", "[google mail]/all mail"}


def _default_include(name: str, flags: list[str], gmail: bool = False) -> bool:
    if {f.lower() for f in flags} & SKIP_SPECIAL:
        return False
    if gmail:  # every label is a folder in Gmail: All Mail holds everything once, the inbox shows what's in it
        return name.lower() in GMAIL_DEFAULTS or "\\all" in {f.lower() for f in flags}
    return name.rsplit("/", 1)[-1].rsplit(".", 1)[-1].lower() not in SKIP_NAMES


def test_imap(settings: dict, password: str) -> list[dict]:
    """Log in, list folders and open each read-only. Returns folders with message counts, or raises
    ConnectionProblem with a reason a person can act on."""
    from towpath.adapters.imap import ReadOnlyViolation, connect_session

    try:
        from imapclient.exceptions import IMAPClientError, LoginError
    except ImportError:
        raise ConnectionProblem("This image lacks IMAP support.") from None
    if not password:
        raise ConnectionProblem("Enter the app password.")
    source = _imap_source("setup-test", {**settings, "timeout_seconds": 20}, "none")
    where = f"{source.host}:{source.port}"
    try:
        session = connect_session(source, password)
    except LoginError:
        raise ConnectionProblem("The server rejected the address or password. Check both, and use an app "
                                "password, not your account password.") from None
    except socket.gaierror:
        raise ConnectionProblem(f"Couldn't find the server {source.host}. Check the server name.") from None
    except ssl.SSLError:
        raise ConnectionProblem(f"Couldn't make a secure connection to {where}.") from None
    except (TimeoutError, socket.timeout):
        raise ConnectionProblem(f"{where} didn't answer in time.") from None
    except (OSError, IMAPClientError):
        raise ConnectionProblem(f"Couldn't connect to {where}.") from None
    try:
        folders = []
        for name, flags in session.folders():
            info = session.examine(name)
            special = next((f for f in flags if f.lower() in {"\\trash", "\\junk", "\\drafts", "\\sent",
                                                               "\\archive", "\\all", "\\flagged"}), None)
            folders.append({"name": name, "messages": int(info["exists"] or 0),
                            "special": special.lstrip("\\") if special else None,
                            "include": _default_include(name, flags, gmail=source.host == "imap.gmail.com")})
    except ReadOnlyViolation:
        raise ConnectionProblem("The server would not open folders read-only, so Towpath won't use it.") from None
    except (OSError, IMAPClientError):
        raise ConnectionProblem(f"The connection to {where} failed while listing folders. Try again.") from None
    finally:
        session.logout()
    if not folders:
        raise ConnectionProblem("The account has no folders Towpath can read.")
    return folders


def test_write_imap(settings: dict, password: str) -> dict:
    """Check a password meant for changing mail: it must open the inbox read-write, and the server must offer
    MOVE. Opens the inbox and leaves it again without changing or expunging anything (UNSELECT, or EXAMINE)."""
    from towpath.mail.executor import ActSession, WriteViolation, connect_act

    try:
        from imapclient.exceptions import IMAPClientAbortError, IMAPClientError, LoginError
    except ImportError:
        raise ConnectionProblem("This image lacks IMAP support.") from None
    if not password:
        raise ConnectionProblem("Enter the app password.")
    source = _imap_source("setup-test", {**settings, "timeout_seconds": 20}, "none")
    where = f"{source.host}:{source.port}"
    try:
        session: ActSession = connect_act(source, password)
    except LoginError:
        raise ConnectionProblem("The server rejected that password. Make a new app password.") from None
    except (OSError, IMAPClientError):
        raise ConnectionProblem(f"Couldn't connect to {where}.") from None
    try:
        inbox = next((n for n, _ in session.folders() if n.upper() == "INBOX"), None)
        if inbox is None:
            raise ConnectionProblem("The account has no inbox.")
        try:
            session.select(inbox)
        except IMAPClientAbortError:
            raise
        except (WriteViolation, IMAPClientError):  # opened read-only, or refused outright
            raise ConnectionProblem("That password can only read mail. Make an app password that can change mail "
                                    "too (in Fastmail: Mail, with full access).") from None
        session.unselect()
        caps = sorted(session.capabilities())
        if "MOVE" not in caps:
            raise ConnectionProblem("The server doesn't offer MOVE, which Towpath needs to archive mail safely.")
        specials = {use: session.special(f"\\{use}") for use in ("Archive", "Trash", "Drafts")}
    except (OSError, IMAPClientError):
        raise ConnectionProblem(f"The connection to {where} failed while checking the password. Try again.") from None
    finally:
        session.logout()
    return {"tested_at": _now(), "capabilities": [c for c in caps if c in ("MOVE", "UIDPLUS", "UNSELECT", "IDLE",
                                                                          "CONDSTORE", "OBJECTID", "SPECIAL-USE")],
            "mailboxes": specials}


def save_write_password(store_dir: Path, credentials_dir: Path, source_id: str, password: str, tested: dict) -> None:
    """Store a tested write password. It must differ from the read-only one, which must stay read-only."""
    c = _require(store_dir, source_id)
    if c["adapter"] != "imap":
        raise ConnectionProblem("Only email accounts reached over IMAP take a write password.")
    read = secret_path(credentials_dir, source_id)
    if read.is_file() and read.read_text() == password:
        raise ConnectionProblem("That's the same password Towpath reads mail with. Use a separate app password, so "
                                "the reading connection can never change mail.")
    _write_secret(write_secret_path(credentials_dir, source_id), password)
    _update(store_dir, source_id, "write-password-added", settings={**c["settings"], "write": tested})


def remove_write_password(store_dir: Path, credentials_dir: Path, source_id: str) -> None:
    c = _require(store_dir, source_id)
    write_secret_path(credentials_dir, source_id).unlink(missing_ok=True)
    _update(store_dir, source_id, "write-password-removed",
            settings={k: v for k, v in c["settings"].items() if k != "write"})


def add_imap(store_dir: Path, credentials_dir: Path, provider: str, settings: dict, password: str,
             folders: list[dict], taken: set[str] = frozenset()) -> str:
    """Record a tested account and store its password. Folders are chosen next; nothing is indexed yet."""
    if provider not in PROVIDERS:
        raise ConnectionProblem("Unknown provider.")
    sid = _new_id(store_dir, provider, set(taken))
    _imap_source(sid, settings, "none")  # validates the settings before anything is written
    _write_secret(secret_path(credentials_dir, sid), password)
    label = PROVIDERS[provider]["label"] if provider != "imap" else settings["host"]
    now = _now()
    with closing(open_store(store_dir, "connections", ROLE)) as db:
        db.execute("""INSERT INTO connections (source_id, provider, adapter, display_name, settings, folders,
                      state, indexing, created_at, updated_at)
                      VALUES (?, ?, 'imap', ?, ?, ?, 'choose-folders', 'idle', ?, ?)""",
                   (sid, provider, label, json.dumps(settings), json.dumps(folders), now, now))
        db.execute("INSERT INTO connection_events (source_id, at, event) VALUES (?, ?, 'added')", (sid, now))
        db.commit()
    return sid


def replace_password(store_dir: Path, credentials_dir: Path, source_id: str, password: str,
                     folders: list[dict]) -> None:
    """Store a new, already tested password; refresh the folder list."""
    _require(store_dir, source_id)
    _write_secret(secret_path(credentials_dir, source_id), password)
    _update(store_dir, source_id, "password-replaced", folders=folders, last_error=None,
            state="ready" if get(store_dir, source_id)["settings"].get("mailboxes") else "choose-folders")


def choose_folders(store_dir: Path, source_id: str, names: list[str]) -> None:
    found = _require(store_dir, source_id)
    known = {f["name"] for f in found["folders"] or ()}
    chosen = [n for n in names if n in known]
    if not chosen:
        raise ConnectionProblem("Choose at least one folder.")
    settings = {**found["settings"], "mailboxes": chosen}
    _update(store_dir, source_id, "folders-chosen", {"folders": len(chosen)}, settings=settings, state="ready")
