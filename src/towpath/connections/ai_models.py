"""AI model connections: the gateway endpoints they offer, and testing and adding them."""

import json
import time
from contextlib import closing
from pathlib import Path

from towpath.connections.base import (
    ROLE,
    ConnectionProblem,
    _new_id,
    _now,
    _require,
    _update,
    _write_secret,
    secret_path,
)
from towpath.stores import open_store

# Where an AI model connection runs: on the owner's network (the default) or at a cloud service the owner chose.
# Either way nothing personal reaches it without the owner's grant; mail marked restricted never reaches a cloud one.
MODEL_DESTINATIONS = {"self-hosted": "On your network", "third-party": "A cloud service"}
# What an AI model connection is used for. Personal content reaches it only after the owner allows it on the Life
# page (a grant in the decisions store, tied to this endpoint's address, model and token file).
MODEL_TASKS = ("life.propose", "life.group", "mail.thread", "mail.draft", "mail.classify", "ask.answer",
               "search.translate")
TASK_LABELS = {"life.propose": "Life story: propose events", "life.group": "Life story: gather events into memories",
               "mail.thread": "Mail: who needs to reply", "mail.draft": "Mail: draft replies",
               "mail.classify": "Mail: categories and rules", "ask.answer": "Ask: answer questions from your records",
               "search.translate": "Search: understand plain-English questions"}
MODEL_TIMEOUT_SECONDS = 300.0  # a model on a sleeping machine can take a minute or two to wake and load


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
