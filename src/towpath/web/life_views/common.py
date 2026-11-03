"""What the Life pages share: the configuration, the accounts, the page's status, and how a claim is shown."""

from django.conf import settings

from urllib.parse import urlencode

from towpath import connections, life_propose
from towpath.unified import grants

_TURN = {3: "rot180", 6: "rot90", 8: "rot270"}  # EXIF orientation to the preview's turn
LIFE_STORES = ("decisions", "connections")
FEATURE = "life-evidence"
TASK = "life.propose"


def _config():
    return connections.merged(settings.TOWPATH_CONFIG, None, role="web")


def _accounts(config) -> list[dict]:
    allowed = grants.granted(settings.TOWPATH_STORE_DIR)
    out = []
    for sid, source in config.sources.items():
        if source.kind == "mail-provider":
            out.append({"source_id": sid, "name": sid, "allowed": FEATURE in allowed.get(sid, set())})
    rows = connections.all_connections(settings.TOWPATH_STORE_DIR, role="web")
    names = {c["source_id"]: c["display_name"] for c in rows}
    out += [{"source_id": c["source_id"], "name": "Photos in your folders" if c["adapter"] == "recoll" else
             c["display_name"], "allowed": FEATURE in allowed.get(c["source_id"], set())}
            for c in rows if c["adapter"] in ("dav", "recoll")]
    for account in out:
        account["name"] = names.get(account["source_id"], account["name"])
    return out


SHOW = {"review": ("proposed",), "minor": ("proposed",), "kept": ("accepted", "corrected"), "rejected": ("rejected",),
        "all": ("proposed", "accepted", "corrected", "rejected")}


def _shown(claim, show: str) -> bool:
    """Whether a claim belongs under a filter: everyday events the model judged routine wait under Minor."""
    if claim.state not in SHOW.get(show, SHOW["review"]):
        return False
    if show == "minor":
        return claim.significance == "routine"
    return show != "review" or claim.significance != "routine"


def _status() -> dict:
    """The proposal job's state and why it can't run, for the page and its live panel."""
    config = _config()
    value = life_propose.request(settings.TOWPATH_STORE_DIR)
    run = life_propose.progress(settings.TOWPATH_STORE_DIR)
    if run is not None and value is not None and run["run_id"] != life_propose.run_id(value):
        run = None  # that run was for an earlier slice
    reason = life_propose.blocked(config, value, role="web")
    running = reason is None and not (run and run["state"] == "done")
    on = life_propose.auto(settings.TOWPATH_STORE_DIR, role="web")
    once = life_propose.run_summary(settings.TOWPATH_STORE_DIR)
    if once["state"] == "ready":
        once["can_start"] = life_propose.can_run(config)
    awaiting_result = once["state"] == "ended" and not once.get("at_end")
    return {"request": value, "run": run, "blocked": reason, "running": running or on or awaiting_result, "auto": on,
            "auto_progress": life_propose.auto_progress(settings.TOWPATH_STORE_DIR) if on else None,
            "run_once": once}


def _row(claim) -> dict:
    citation = claim.citations[0] if claim.citations else None
    return {"id": claim.claim_id, "statement": claim.statement, "when": claim.when.expression,
            "earliest": claim.when.earliest, "latest": claim.when.latest, "precision": claim.when.precision,
            "modality": {"plan": "planned", "occurred": "happened"}.get(claim.modality, claim.modality),
            "state": claim.state, "uncertainty": claim.uncertainty, "significance": claim.significance,
            "quote": citation.captured_excerpt if citation else None,
            "evidence_url": "/ref/?" + urlencode({"r": citation.ref}) if citation and citation.ref else None,
            "year": (claim.when.earliest or "")[:4] or "Undated"}
