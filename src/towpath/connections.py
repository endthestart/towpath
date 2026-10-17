"""Accounts a person adds in the UI. Runs only in towpath-connect, the service that holds credentials.

Non-secret settings (provider, address, server, chosen folders), indexing state and progress live in the
``connections`` store, which the web service may read. Each secret lives in the connector's credentials
folder as one file, mode 0600, and is never stored elsewhere, displayed, logged or returned: it can only be
replaced or deleted. See docs/specs/connections-in-the-ui.md.

Indexing runs only after the owner starts it. The request worker runs it in bounded slices, so searches and
content requests are not held up; Pause takes effect within seconds and every stop is resumable.
"""

import dataclasses
import json
import os
import re
import secrets
import socket
import ssl
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from towpath.stores import open_store

ROLE = "connect"
PROVIDERS = {
    "gmail": {"label": "Gmail", "host": None, "port": None, "security": None,
              "help_url": "https://myaccount.google.com/connections"},
    "fastmail": {"label": "Fastmail", "host": "imap.fastmail.com", "port": 993, "security": "tls",
                 "help_url": "https://www.fastmail.help/hc/en-us/articles/360058752854-App-passwords"},
    "imap": {"label": "Email (IMAP)", "host": None, "port": 993, "security": "tls", "help_url": None},
    "folders": {"label": "Folders on this server", "host": None, "port": None, "security": None, "help_url": None},
    "model": {"label": "AI model on your network", "host": None, "port": None, "security": None, "help_url": None},
}
# Where an AI model connection runs: on the owner's network (the default) or at a cloud service the owner chose.
# Either way nothing personal reaches it without the owner's grant; mail marked restricted never reaches a cloud one.
MODEL_DESTINATIONS = {"self-hosted": "On your network", "third-party": "A cloud service"}
# What an AI model connection is used for. Personal content reaches it only after the owner allows it on the Life
# page (a grant in the decisions store, tied to this endpoint's address, model and token file).
MODEL_TASKS = ("life.propose", "mail.thread", "mail.draft", "mail.classify", "ask.answer", "search.translate")
TASK_LABELS = {"life.propose": "Life story: propose events", "mail.thread": "Mail: who needs to reply",
               "mail.draft": "Mail: draft replies", "mail.classify": "Mail: categories and rules",
               "ask.answer": "Ask: answer questions from your records",
               "search.translate": "Search: understand plain-English questions"}
MODEL_TIMEOUT_SECONDS = 300.0  # a model on a sleeping machine can take a minute or two to wake and load
# Folders left out by default: special use (RFC 6154) first, then common names for servers that omit it.
SKIP_SPECIAL = {"\\trash", "\\junk", "\\drafts"}
SKIP_NAMES = {"trash", "deleted items", "deleted messages", "spam", "junk", "junk mail", "junk e-mail", "drafts"}
SLICE_SECONDS = 300
CHECK_EVERY_SECONDS = 3.0


class ConnectionProblem(Exception):
    """A plain-language reason the connection could not be made or used. Never contains a secret."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def secret_path(credentials_dir: Path, source_id: str) -> Path:
    return Path(credentials_dir) / f"{source_id}.password"


def write_secret_path(credentials_dir: Path, source_id: str) -> Path:
    """The second app password that mail management's action runner uses to change mail (towpath.mail.executor)."""
    return Path(credentials_dir) / f"{source_id}.act-password"


GOOGLE_CLIENT_FILE = "google-web-client.json"


def token_path(credentials_dir: Path, c: dict) -> Path:
    """A Gmail connection's OAuth token file, inside the credentials folder."""
    return Path(credentials_dir) / c["settings"]["token"]


def has_credential(credentials_dir: Path, c: dict) -> bool:
    if c["adapter"] == "recoll":
        return True  # folders on the NAS need no sign-in
    path = token_path(credentials_dir, c) if c["adapter"] == "gmail" else secret_path(credentials_dir, c["source_id"])
    return path.is_file()


def _write_secret(path: Path, value: str) -> None:
    """Atomically replace ``path`` with ``value``, readable only by this service."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


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


def _default_include(name: str, flags: list[str]) -> bool:
    if {f.lower() for f in flags} & SKIP_SPECIAL:
        return False
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
        raise ConnectionProblem("The server rejected the address or password. Check both; for Fastmail, use an "
                                "app password, not your account password.") from None
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
                            "include": _default_include(name, flags)})
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
        raise ConnectionProblem("The server rejected that password. For Fastmail, make a new app password.") from None
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
                                    "too (for Fastmail: Mail, with full access).") from None
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


# -- the connections store ------------------------------------------------------------------------------


def _row(row) -> dict:
    out = dict(row)
    for key in ("settings", "folders", "progress"):
        out[key] = json.loads(out[key]) if out.get(key) else None
    return out


def all_connections(store_dir: Path, role: str = ROLE) -> list[dict]:
    with closing(open_store(store_dir, "connections", role)) as db:
        return [_row(r) for r in db.execute("SELECT * FROM connections ORDER BY created_at")]


def get(store_dir: Path, source_id: str, role: str = ROLE) -> dict | None:
    with closing(open_store(store_dir, "connections", role)) as db:
        row = db.execute("SELECT * FROM connections WHERE source_id = ?", (source_id,)).fetchone()
    return _row(row) if row else None


def _update(store_dir: Path, source_id: str, event: str | None = None, detail: dict | None = None, **fields):
    with closing(open_store(store_dir, "connections", ROLE)) as db:
        values = {k: json.dumps(v) if k in ("settings", "folders", "progress") and v is not None else v
                  for k, v in fields.items()}
        values["updated_at"] = _now()
        db.execute(f"UPDATE connections SET {', '.join(f'{k} = ?' for k in values)} WHERE source_id = ?",
                   (*values.values(), source_id))
        if event:
            db.execute("INSERT INTO connection_events (source_id, at, event, detail) VALUES (?, ?, ?, ?)",
                       (source_id, _now(), event, json.dumps(detail) if detail else None))
        db.commit()


def _new_id(store_dir: Path, provider: str, taken: set[str]) -> str:
    taken = taken | {c["source_id"] for c in all_connections(store_dir)}
    base = re.sub(r"[^a-z0-9-]", "-", provider.lower())
    n, sid = 1, base
    while sid in taken:
        n += 1
        sid = f"{base}-{n}"
    return sid


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


def _require(store_dir: Path, source_id: str) -> dict:
    found = get(store_dir, source_id)
    if found is None:
        raise ConnectionProblem("No such connection.")
    return found


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


def start_indexing(store_dir: Path, credentials_dir: Path, source_id: str) -> None:
    found = _require(store_dir, source_id)
    if found["state"] != "ready":
        raise ConnectionProblem("Choose folders first." if found["state"] == "choose-folders"
                                else "Reconnect the account first.")
    if not has_credential(credentials_dir, found):
        raise ConnectionProblem("The sign-in is missing; reconnect first." if found["adapter"] == "gmail"
                                else "The password is missing; replace it first.")
    if found["indexing"] not in ("requested", "running"):
        _update(store_dir, source_id, "indexing-requested", indexing="requested", last_error=None)


# How often a connection checks for changes by itself, once the owner chooses it on its page (off by default).
SCHEDULES = {"off": ("Off: only when you press the button", None), "15m": ("Every 15 minutes", 15 * 60),
             "1h": ("Every hour", 3600), "6h": ("Every 6 hours", 6 * 3600), "1d": ("Every day", 86400),
             "7d": ("Every week", 7 * 86400)}
MAIL_SCHEDULES = ("off", "15m", "1h", "6h", "1d")
FOLDER_SCHEDULES = ("off", "1d", "7d")  # reading every folder again takes long; at most daily


def schedules_for(c: dict) -> tuple[str, ...]:
    return FOLDER_SCHEDULES if c["adapter"] == "recoll" else MAIL_SCHEDULES


def set_schedule(store_dir: Path, source_id: str, schedule: str) -> None:
    found = _require(store_dir, source_id)
    if schedule not in schedules_for(found):
        raise ConnectionProblem("Choose one of the listed schedules.")
    _update(store_dir, source_id, "schedule-set", {"schedule": schedule},
            settings={**found["settings"], "schedule": schedule})


def _last_start(db, source_id: str) -> str | None:
    row = db.execute("SELECT max(at) FROM connection_events WHERE source_id = ? AND event IN "
                     "('indexing-requested', 'indexing-scheduled')", (source_id,)).fetchone()
    return row[0] if row else None


def next_check(store_dir: Path, c: dict, role: str = ROLE) -> datetime | None:
    """When the connection's schedule next starts it, or None when it has no schedule."""
    seconds = SCHEDULES.get((c["settings"] or {}).get("schedule") or "off", (None, None))[1]
    if not seconds:
        return None
    with closing(open_store(store_dir, "connections", role)) as db:
        last = _last_start(db, c["source_id"])
    never = datetime.fromtimestamp(0, timezone.utc)  # never started: due at once
    return datetime.fromisoformat(last) + timedelta(seconds=seconds) if last else never


def start_scheduled(store_dir: Path, credentials_dir: Path, now: datetime | None = None) -> list[dict]:
    """Start each idle, ready connection whose schedule is due. A paused connection stays paused."""
    now = now or datetime.now(timezone.utc)
    started = []
    for c in all_connections(store_dir):
        if c["state"] != "ready" or c["indexing"] != "idle":
            continue
        due = next_check(store_dir, c)
        if due is None or due > now or not has_credential(credentials_dir, c):
            continue
        _update(store_dir, c["source_id"], "indexing-scheduled", indexing="requested", last_error=None)
        started.append({"source_id": c["source_id"], "step": "scheduled"})
    return started


def pause_indexing(store_dir: Path, source_id: str) -> None:
    if _require(store_dir, source_id)["indexing"] in ("requested", "running"):
        _update(store_dir, source_id, "indexing-paused", indexing="paused")


def disconnect(store_dir: Path, credentials_dir: Path, source_id: str) -> None:
    """Delete the stored secret. The index stays, labelled disconnected; nothing is deleted at the provider."""
    c = _require(store_dir, source_id)
    (token_path(credentials_dir, c) if c["adapter"] == "gmail" else secret_path(credentials_dir, source_id)).unlink(
        missing_ok=True)
    write_secret_path(credentials_dir, source_id).unlink(missing_ok=True)
    _update(store_dir, source_id, "disconnected", state="disconnected", indexing="idle",
            settings={k: v for k, v in c["settings"].items() if k != "write"})


def merged(config, credentials_dir: Path | None, role: str = ROLE):
    """``config`` with every connection added as a source, and the instance's Gmail pacing if it was set in the
    UI. A connection imported from the configuration file replaces that entry. Without a credentials folder
    (the web service) the sources carry no credential reference at all, so nothing there can find a secret."""
    from towpath import config as config_mod

    added = {}
    for c in all_connections(config.store_dir, role):
        sid = c["source_id"]
        if sid in config.sources and c["settings"].get("origin") != "config":
            continue
        if c["adapter"] == "imap":
            ref = f"file:{secret_path(credentials_dir, sid)}" if credentials_dir else "none"
            added[sid] = _imap_source(sid, c["settings"], ref)
        elif c["adapter"] == "gmail":
            added[sid] = config_mod.Source(sid, "mail-provider", "gmail",
                                           token=token_path(credentials_dir, c) if credentials_dir else None)
    models = [c for c in all_connections(config.store_dir, role) if c["adapter"] == "model" and c["state"] == "ready"]
    endpoints = {e.id: e for c in models for e in model_endpoints(config.store_dir, c)}
    from towpath.decisions import task_models

    chosen = task_models(config.store_dir, role=role)
    tasks = {task: chosen[task] if chosen.get(task) in endpoints else models[0]["source_id"]
             for task in MODEL_TASKS if models and task not in config.tasks}
    pacing = gmail_pacing(config, role)
    if added or endpoints or pacing is not config.gmail_pacing:
        config = dataclasses.replace(config, sources={**config.sources, **added}, gmail_pacing=pacing,
                                     endpoints={**config.endpoints, **endpoints}, tasks={**config.tasks, **tasks})
    from towpath import folders

    return folders.with_files(config, Path(config.store_dir).parent / "index", role)


# -- AI models -------------------------------------------------------------------------------------------


def model_endpoint(store_dir: Path, c: dict):
    """The gateway endpoint for an AI model connection. The token reference is the same in every service (the
    data folder's credentials/), so a grant recorded by the web service matches what the worker checks."""
    from towpath import config as config_mod

    s = c["settings"]
    ref = f"file:{secret_path(Path(store_dir).parent / 'credentials', c['source_id'])}"
    destination = s.get("destination") if s.get("destination") in MODEL_DESTINATIONS else "self-hosted"
    return config_mod.Endpoint(c["source_id"], s["base_url"].rstrip("/"), ref, s["model"], "chat", destination,
                               ("synthetic", "metadata", "content"), MODEL_TIMEOUT_SECONDS)


def model_endpoints(store_dir: Path, c: dict) -> list:
    """The connection's main model, then each further model on the same server (same address and token). A
    further model's endpoint ID is ``<connection>:<model name>``; each has its own grants."""
    from towpath import config as config_mod

    main = model_endpoint(store_dir, c)
    out = [main]
    for name, extra in sorted((c["settings"].get("models") or {}).items()):
        destination = extra.get("destination") if extra.get("destination") in MODEL_DESTINATIONS else main.destination
        out.append(config_mod.Endpoint(f"{c['source_id']}:{name}", main.base_url, main.credential, name, "chat",
                                       destination, main.allow_data, MODEL_TIMEOUT_SECONDS))
    return out


def add_model_name(store_dir: Path, credentials_dir: Path, source_id: str, model: str,
                   destination: str | None = None) -> dict:
    """Test another model on an existing model connection's server, with its stored token, and add it."""
    c = _require(store_dir, source_id)
    if c["adapter"] != "model":
        raise ConnectionProblem("Only an AI model connection can offer further models.")
    model = model.strip()
    if not model or model == c["settings"]["model"]:
        raise ConnectionProblem("Enter the name of a different model on the same server.")
    destination = destination or c["settings"].get("destination") or "self-hosted"
    if destination not in MODEL_DESTINATIONS:
        raise ConnectionProblem("Choose where the model runs.")
    path = secret_path(credentials_dir, source_id)
    token = path.read_text() if path.is_file() else ""
    tested = test_model({"base_url": c["settings"]["base_url"], "model": model}, token)
    models = {**(c["settings"].get("models") or {}), model: {"tested": tested, "destination": destination}}
    _update(store_dir, source_id, "model-added", {"model": model}, settings={**c["settings"], "models": models})
    return tested


def remove_model_name(store_dir: Path, source_id: str, model: str) -> None:
    c = _require(store_dir, source_id)
    models = {k: v for k, v in (c["settings"].get("models") or {}).items() if k != model}
    _update(store_dir, source_id, "model-removed", {"model": model}, settings={**c["settings"], "models": models})


def test_model(settings: dict, token: str) -> dict:
    """Ask the model to answer once, with a synthetic prompt only. Raises ConnectionProblem in plain words."""
    import openai

    base = settings["base_url"].rstrip("/")
    if not base.startswith(("http://", "https://")):
        raise ConnectionProblem("The address starts with http:// or https://, for example http://poundlock:7331/v1.")
    client = openai.OpenAI(base_url=base, api_key=token or "not-used", timeout=MODEL_TIMEOUT_SECONDS, max_retries=0)
    started = time.monotonic()
    try:
        listed = [m.id for m in client.models.list()]
        if listed and settings["model"] not in listed:
            raise ConnectionProblem(f"That token can't use a model named {settings['model']!r}. Check the name, "
                                    "or allow the model for this token.")
        reply = client.chat.completions.create(model=settings["model"], max_tokens=8,
                                               messages=[{"role": "user", "content": "Reply with the word ok."}])
    except openai.AuthenticationError:
        raise ConnectionProblem("The model server refused the token.") from None
    except openai.APIConnectionError:
        raise ConnectionProblem("Towpath couldn't reach that address.") from None
    except openai.APITimeoutError:
        raise ConnectionProblem("The model didn't answer in time; if its machine was asleep, try again.") from None
    except openai.APIStatusError as exc:
        raise ConnectionProblem(f"The model server answered with an error ({exc.status_code}).") from None
    if not reply.choices:
        raise ConnectionProblem("The model answered with nothing.")
    return {"seconds": round(time.monotonic() - started, 1), "listed": bool(listed), "at": _now()}


def add_model(store_dir: Path, credentials_dir: Path, settings: dict, token: str, tested: dict,
              taken: set[str] = frozenset()) -> str:
    """Record a tested AI model connection and keep its token in the credentials folder."""
    sid = _new_id(store_dir, "model", set(taken))
    _write_secret(secret_path(credentials_dir, sid), token)
    destination = settings.get("destination") or "self-hosted"
    if destination not in MODEL_DESTINATIONS:
        raise ConnectionProblem("Choose where the model runs.")
    settings = {"base_url": settings["base_url"].rstrip("/"), "model": settings["model"], "tested": tested,
                "destination": destination}
    now = _now()
    with closing(open_store(store_dir, "connections", ROLE)) as db:
        db.execute("""INSERT INTO connections (source_id, provider, adapter, display_name, settings, folders,
                      state, indexing, created_at, updated_at)
                      VALUES (?, 'model', 'model', ?, ?, NULL, 'ready', 'idle', ?, ?)""",
                   (sid, settings["model"], json.dumps(settings), now, now))
        db.execute("INSERT INTO connection_events (source_id, at, event) VALUES (?, ?, 'added')", (sid, now))
        db.commit()
    return sid


# -- Gmail -----------------------------------------------------------------------------------------------


def _setting(store_dir: Path, key: str, role: str = ROLE):
    import sqlite3

    with closing(open_store(store_dir, "connections", role)) as db:
        try:
            row = db.execute("SELECT value FROM instance_settings WHERE key = ?", (key,)).fetchone()
        except sqlite3.OperationalError:  # a store written before this setting existed
            return None
    return json.loads(row[0]) if row else None


def gmail_pacing(config, role: str = ROLE):
    """The Gmail pacing in force: set on the Connections page, else the configuration file's."""
    from towpath.quota import PacingSettings

    saved = _setting(config.store_dir, "gmail_pacing", role)
    return PacingSettings.from_table(saved) if saved else config.gmail_pacing


def set_verified_quota(config, verified: int | None) -> None:
    """Pace Gmail from the per-user quota shown in Google Cloud Console: 30% of it, never above 1,800 units a
    minute. ``None`` returns to Google's default pacing. Invalid values are refused, not adjusted."""
    from towpath.quota import PacingSettings

    current = dataclasses.asdict(gmail_pacing(config))
    if verified is None:
        defaults = dataclasses.asdict(PacingSettings())
        table = {**current, "verified_units_per_minute": None, "units_per_minute": defaults["units_per_minute"],
                 "min_interval_seconds": defaults["min_interval_seconds"]}
    else:
        table = {**current, "verified_units_per_minute": verified,
                 "units_per_minute": min(1800, verified * 3 // 10),
                 "min_interval_seconds": min(current["min_interval_seconds"], 0.667)}
    table = {k: v for k, v in table.items() if v is not None}
    try:
        PacingSettings.from_table(table)
    except ValueError as exc:
        raise ConnectionProblem(str(exc).split(" (got")[0].capitalize() + ".") from None
    with closing(open_store(config.store_dir, "connections", ROLE)) as db:
        db.execute("INSERT OR REPLACE INTO instance_settings (key, value, updated_at) VALUES ('gmail_pacing', ?, ?)",
                   (json.dumps(table), _now()))
        db.commit()


def import_configured(config, credentials_dir: Path) -> list[str]:
    """Bring Gmail sources and Gmail pacing from the configuration file onto the Connections page, once. The
    index, source ID, quota history and token stay as they are: no re-consent and no re-indexing."""
    import shutil

    from towpath.quota import PacingSettings

    if _setting(config.store_dir, "gmail_pacing") is None and config.gmail_pacing != PacingSettings():
        table = {k: v for k, v in dataclasses.asdict(config.gmail_pacing).items() if v is not None}
        with closing(open_store(config.store_dir, "connections", ROLE)) as db:
            db.execute("INSERT OR IGNORE INTO instance_settings (key, value, updated_at) VALUES ('gmail_pacing', ?, ?)",
                       (json.dumps(table), _now()))
            db.commit()
    imported = []
    known = {c["source_id"] for c in all_connections(config.store_dir)}
    for sid, source in config.sources.items():
        if source.adapter != "gmail" or sid in known or source.token is None:
            continue
        token = Path(source.token)
        if token.parent.resolve() == Path(credentials_dir).resolve():
            name = token.name
        else:
            name = f"{sid}.token.json"
            if token.is_file():
                shutil.copy2(token, Path(credentials_dir) / name)
                os.chmod(Path(credentials_dir) / name, 0o600)
        with closing(open_store(config.store_dir, "source", "connect")) as db:
            total = db.execute("SELECT COUNT(*) FROM items WHERE source_id = ? AND absent_since_run IS NULL",
                               (sid,)).fetchone()[0]
            last = db.execute("SELECT MAX(finished_at) FROM runs WHERE source_id = ? AND complete = 1",
                              (sid,)).fetchone()[0]
        now = _now()
        progress = {"phase": "imported", "processed": 0, "rate": None, "indexed": total, "at": last or now}
        with closing(open_store(config.store_dir, "connections", ROLE)) as db:
            db.execute("""INSERT OR IGNORE INTO connections (source_id, provider, adapter, display_name, settings,
                          state, indexing, progress, created_at, updated_at)
                          VALUES (?, 'gmail', 'gmail', 'Gmail', ?, 'ready', 'idle', ?, ?, ?)""",
                       (sid, json.dumps({"token": name, "origin": "config", "username": None}), json.dumps(progress),
                        now, now))
            db.execute("INSERT INTO connection_events (source_id, at, event) VALUES (?, ?, 'imported')", (sid, now))
            db.commit()
        imported.append(sid)
    return imported


def google_client(credentials_dir: Path) -> dict | None:
    """The instance's Google OAuth web client, if one was entered (its ID is shown; its secret never is)."""
    path = Path(credentials_dir) / GOOGLE_CLIENT_FILE
    return json.loads(path.read_text())["web"] if path.is_file() else None


def save_google_client(credentials_dir: Path, client_id: str, client_secret: str, redirect_uri: str) -> None:
    client_id, client_secret = client_id.strip(), client_secret.strip()
    if not client_id.endswith(".apps.googleusercontent.com"):
        raise ConnectionProblem("That doesn't look like a Google client ID; it ends in .apps.googleusercontent.com.")
    if not client_secret:
        raise ConnectionProblem("Enter the client secret.")
    _write_secret(Path(credentials_dir) / GOOGLE_CLIENT_FILE, json.dumps({"web": {
        "client_id": client_id, "client_secret": client_secret, "redirect_uris": [redirect_uri],
        "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": "https://oauth2.googleapis.com/token"}}))


def save_gmail(store_dir: Path, credentials_dir: Path, token_json: str, address: str | None,
               reconnect: str | None = None, taken: set[str] = frozenset()) -> str:
    """Store a verified read-only Gmail token as a new connection, or as a reconnection of ``reconnect``."""
    if reconnect is not None:
        c = _require(store_dir, reconnect)
        known = c["settings"].get("username")
        if known and address and known.lower() != address.lower():
            raise ConnectionProblem(f"That signed in as {address}, but this connection is {known}. "
                                    "Sign in with the same Google account, or add it as a new connection.")
        _write_secret(token_path(credentials_dir, c), token_json)
        settings = {**c["settings"], "username": address or known}
        _update(store_dir, reconnect, "reconnected", settings=settings, state="ready", last_error=None)
        return reconnect
    sid = _new_id(store_dir, "gmail", set(taken))
    name = f"{sid}.token.json"
    _write_secret(Path(credentials_dir) / name, token_json)
    now = _now()
    with closing(open_store(store_dir, "connections", ROLE)) as db:
        db.execute("""INSERT INTO connections (source_id, provider, adapter, display_name, settings, state, indexing,
                      created_at, updated_at) VALUES (?, 'gmail', 'gmail', 'Gmail', ?, 'ready', 'idle', ?, ?)""",
                   (sid, json.dumps({"token": name, "username": address}), now, now))
        db.execute("INSERT INTO connection_events (source_id, at, event) VALUES (?, ?, 'added')", (sid, now))
        db.commit()
    return sid


# -- indexing (request worker) --------------------------------------------------------------------------


def _plain_stop(termination: str, reason: str | None, adapter: str | None = None) -> str:
    if termination == "auth-stop" and adapter == "gmail":
        return "Google's sign-in for this account has expired. Choose Reconnect Gmail below to sign in again."
    if termination == "auth-stop":
        return "The account rejected Towpath's stored sign-in. Reconnect or replace the password to continue."
    if termination in ("quota-stop", "daily-budget-stop"):
        return "Paused at Gmail's quota limit. Start again later; nothing is lost."
    if termination == "lock-busy":
        return "Another Gmail job was running. Start again in a moment."
    if termination == "server-stop":
        return "The server or network stopped answering. Indexing will resume when you start it again."
    return reason or termination


def _tracker(store_dir: Path, sid: str, deadline: float, clock):
    """Progress that saves counts (never content) for the UI and ends the slice on Pause or at the deadline."""
    from towpath.connect import Capped
    from towpath.progress import Progress

    class Tracker(Progress):
        stop = None

        def __init__(self):
            super().__init__(sid, self.record, every_items=25, every_seconds=5.0)
            self.last_check = clock()

        def item(self):
            super().item()
            now = clock()
            if now - self.last_check >= CHECK_EVERY_SECONDS:
                self.last_check = now
                if (get(store_dir, sid) or {}).get("indexing") != "running":
                    self.stop = "paused"
                    raise Capped("paused by the owner; resume to continue")
            if now >= deadline:
                self.stop = "slice"
                raise Capped("slice finished; indexing continues")

        @staticmethod
        def record(snapshot):
            with closing(open_store(store_dir, "source", "connect")) as db:
                total = db.execute("SELECT COUNT(*) FROM items WHERE source_id = ? AND absent_since_run IS NULL",
                                   (sid,)).fetchone()[0]
            _update(store_dir, sid, progress={"phase": snapshot["phase"], "processed": snapshot["processed"],
                                              "rate": snapshot["rate"], "indexed": total, "at": _now()})

    return Tracker()


def index_pending(config, credentials_dir: Path, slice_seconds: float = SLICE_SECONDS,
                  clock=time.monotonic) -> list[dict]:
    """Run one bounded slice for each connection the owner asked to index. Returns a summary per slice."""
    from towpath import connect
    from towpath.adapters.errors import SyncStop

    store_dir = config.store_dir
    summaries = []
    for c in all_connections(store_dir):
        if c["indexing"] not in ("requested", "running") or c["state"] != "ready" or c["adapter"] == "recoll":
            continue  # folders are indexed by towpath.folders
        sid = c["source_id"]
        _update(store_dir, sid, "indexing-slice" if c["indexing"] == "running" else "indexing-started",
                indexing="running")
        tracker = _tracker(store_dir, sid, clock() + slice_seconds, clock)
        try:
            result = connect.sync(merged(config, credentials_dir), sid, progress=tracker)
        except SyncStop as exc:  # raised before sync could record it, such as a sign-in that has expired
            _update(store_dir, sid, "indexing-stopped", {"termination": exc.code}, indexing="idle",
                    last_error=_plain_stop(exc.code, exc.reason, c["adapter"]))
            summaries.append({"source_id": sid, "termination": exc.code})
            continue
        except Exception as exc:  # noqa: BLE001 - recorded by sync; keep data out of the store
            _update(store_dir, sid, "indexing-error", {"type": type(exc).__name__}, indexing="idle",
                    last_error="Indexing stopped on an unexpected error. Progress is saved; start it again.")
            summaries.append({"source_id": sid, "termination": "error"})
            continue
        termination = result["termination"]
        tracker.record({"phase": "finished", "processed": tracker.processed, "rate": tracker.rate()})
        if termination == "complete":
            _update(store_dir, sid, "indexed", {"total": result["indexed_total"]}, indexing="idle")
        elif not (termination == "capped" and tracker.stop in ("slice", "paused")):
            _update(store_dir, sid, "indexing-stopped", {"termination": termination}, indexing="idle",
                    last_error=_plain_stop(termination, result.get("reason"), c["adapter"]))
        summaries.append({"source_id": sid, "termination": termination, "slice": tracker.stop,
                          "indexed_total": result["indexed_total"]})
    return summaries
