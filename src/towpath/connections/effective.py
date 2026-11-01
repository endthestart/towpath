"""The configuration in force: the configuration file plus every connection added in the UI."""

import dataclasses
from pathlib import Path

from towpath.connections.ai_models import MODEL_TASKS, model_endpoints
from towpath.connections.base import ROLE, all_connections, secret_path, token_path
from towpath.connections.gmail import gmail_pacing
from towpath.connections.imap import _imap_source

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
        elif c["adapter"] in ("facebook", "imessage"):
            added[sid] = config_mod.Source(sid, "mail-provider", c["adapter"], path=Path(c["settings"]["path"]))
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
