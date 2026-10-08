"""Single-account login for the UI. One account per instance; it can do everything the UI can.

The account lives in ``web-login.json`` beside the stores (a salted PBKDF2 hash, never the password).
Until it exists, every page leads to first-run setup, which asks for a one-time setup code that the
server prints to its own log, so only someone who can read the host's logs can claim the instance.
Sessions are signed cookies; changing or resetting the account signs every session out.
"""

import hashlib
import json
import os
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core import signing
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods, require_POST

LOGIN_FILE = "web-login.json"
SECRET_FILE = "web-secret.key"
COOKIE = "towpath_session"
SESSION_SECONDS = 30 * 24 * 3600
MIN_PASSWORD = 12
OPEN_PATHS = {"/setup", "/login", "/assets/app.css"}
# Failed logins slow every attempt and pause the form after a burst, whatever the client claims to be.
FAIL_DELAY_SECONDS = 1.0
FAIL_LIMIT, FAIL_WINDOW_SECONDS = 10, 600

_lock = threading.Lock()
_setup_codes: dict[str, str] = {}
_failures: list[float] = []


def _write_new(path: Path, data: bytes) -> bool:
    """Create ``path`` (0600) only if it does not exist yet; False if another writer got there first."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(tmp, path)
        return True
    except FileExistsError:
        return False
    finally:
        tmp.unlink(missing_ok=True)


def secret_key(store_dir: Path) -> str:
    """The instance's signing key, created once and kept beside the stores so sessions survive restarts."""
    path = Path(store_dir) / SECRET_FILE
    if not path.exists():
        _write_new(path, secrets.token_hex(32).encode())
    return path.read_text().strip()


def account(store_dir: Path) -> dict | None:
    try:
        return json.loads((Path(store_dir) / LOGIN_FILE).read_text())
    except FileNotFoundError:
        return None


def create_account(store_dir: Path, username: str, password: str) -> bool:
    record = {"username": username, "password": make_password(password),
              "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    created = _write_new(Path(store_dir) / LOGIN_FILE, json.dumps(record).encode())
    if created:
        _setup_codes.pop(str(store_dir), None)
    return created


def reset_account(store_dir: Path) -> bool:
    """Remove the account; the next visit starts first-run setup with a new code. Signs everyone out."""
    path = Path(store_dir) / LOGIN_FILE
    existed = path.exists()
    path.unlink(missing_ok=True)
    return existed


def setup_code(store_dir: Path) -> str:
    """The current one-time setup code, created and written to the server log on first need."""
    key = str(store_dir)
    with _lock:
        if key not in _setup_codes:
            _setup_codes[key] = "-".join(secrets.token_hex(3) for _ in range(3))
            print(f"Towpath first-run setup code: {_setup_codes[key]} (enter it at /setup)", flush=True)
        return _setup_codes[key]


def _version(record: dict) -> str:
    return hashlib.sha256(record["password"].encode()).hexdigest()[:16]


def _signer_key() -> str:
    return settings.SECRET_KEY


def signed_in(request, record: dict) -> bool:
    value = request.COOKIES.get(COOKIE)
    if not value:
        return False
    try:
        data = signing.loads(value, key=_signer_key(), salt="towpath.web.session", max_age=SESSION_SECONDS)
    except signing.BadSignature:
        return False
    return data.get("u") == record["username"] and data.get("v") == _version(record)


def _start_session(request, response, record: dict):
    value = signing.dumps({"u": record["username"], "v": _version(record)}, key=_signer_key(),
                          salt="towpath.web.session")
    response.set_cookie(COOKIE, value, max_age=SESSION_SECONDS, httponly=True, samesite="Lax",
                        secure=request.is_secure())
    return response


def _safe_next(request) -> str:
    target = request.POST.get("next") or request.GET.get("next") or "/"
    if not target.startswith("/") or not url_has_allowed_host_and_scheme(target, allowed_hosts=None):
        return "/"
    return target


class LoginRequiredMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not getattr(settings, "TOWPATH_LOGIN_REQUIRED", True) or request.path in OPEN_PATHS:
            return self.get_response(request)
        record = account(settings.TOWPATH_STORE_DIR)
        fragment = request.headers.get("HX-Request") == "true"
        if record is None or not signed_in(request, record):
            if fragment:  # a live panel: send the whole page to sign-in, never swap the login form into the panel
                response = HttpResponse(status=401)
                response["HX-Redirect"] = "/setup" if record is None else "/login"
                return response
        if record is None:
            return redirect("/setup")
        if not signed_in(request, record):
            if request.method != "GET":
                return redirect("/login")
            return redirect("/login?" + urlencode({"next": request.get_full_path()}))
        return self.get_response(request)


@require_http_methods(["GET", "POST"])
def setup(request):
    store_dir = settings.TOWPATH_STORE_DIR
    if account(store_dir) is not None:
        return redirect("/login")
    expected = setup_code(store_dir)
    errors, username = [], ""
    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")
        if not secrets.compare_digest(request.POST.get("code", "").strip().lower(), expected):
            errors.append("The setup code does not match the one in the server log.")
        if not 1 <= len(username) <= 64:
            errors.append("Choose a username of 1 to 64 characters.")
        if len(password) < MIN_PASSWORD:
            errors.append(f"Use a password of at least {MIN_PASSWORD} characters.")
        elif password != request.POST.get("confirm", ""):
            errors.append("The two passwords differ.")
        if not errors:
            if not create_account(store_dir, username, password):
                return redirect("/login")
            return _start_session(request, redirect("/"), account(store_dir))
    response = render(request, "setup.html", {"nav": None, "errors": errors, "username": username,
                                              "min_password": MIN_PASSWORD})
    response.status_code = 400 if errors else 200
    return response


@require_http_methods(["GET", "POST"])
def login(request):
    record = account(settings.TOWPATH_STORE_DIR)
    if record is None:
        return redirect("/setup")
    error = None
    if request.method == "POST":
        now = time.monotonic()
        with _lock:
            _failures[:] = [t for t in _failures if now - t < FAIL_WINDOW_SECONDS]
            paused = len(_failures) >= FAIL_LIMIT
        if paused:
            error = "Too many failed sign-ins. Wait a few minutes, then try again."
        elif (request.POST.get("username", "") == record["username"]
              and check_password(request.POST.get("password", ""), record["password"])):
            return _start_session(request, redirect(_safe_next(request)), record)
        else:
            with _lock:
                _failures.append(now)
            time.sleep(FAIL_DELAY_SECONDS)
            error = "Username or password is incorrect."
    response = render(request, "login.html", {"nav": None, "error": error, "next": _safe_next(request)})
    if error:
        response.status_code = 429 if error.startswith("Too many") else 401
    return response


@require_POST
def logout(request):
    response = redirect("/login")
    response.delete_cookie(COOKIE, samesite="Lax")
    return response
