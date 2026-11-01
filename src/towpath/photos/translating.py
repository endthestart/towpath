"""Turning plain-English questions about photos into filters (worker)."""

import json
from contextlib import closing

from towpath.photos.extraction import now
from towpath.photos.searching import facets
from towpath.stores import open_store, store_path

TASK = "search.translate"
PROMPT = "photos.translate/1"
TRANSLATE_SCHEMA = {"type": "object", "additionalProperties": False,
                    "required": ["camera", "lens", "after", "before", "located", "text"],
                    "properties": {"camera": {"type": ["string", "null"], "maxLength": 80},
                                   "lens": {"type": ["string", "null"], "maxLength": 80},
                                   "after": {"type": ["string", "null"], "maxLength": 10},
                                   "before": {"type": ["string", "null"], "maxLength": 10},
                                   "located": {"type": "boolean"},
                                   "text": {"type": ["string", "null"], "maxLength": 80}}}
TRANSLATE = """Turn the owner's request for photos into search filters. Use only these fields:
- camera: part of a camera name from the list below (or null);
- lens: part of a lens name from the list below (or null);
- after, before: dates as YYYY-MM-DD bounding when the photos were taken (or null);
- located: true only if the request needs photos with a recorded location;
- text: a word that might appear in the photos' folder or file names, such as a place or event (or null).
The request is from the owner; the lists come from their photos' metadata."""


def translate_pending(config) -> list[dict]:
    """Turn waiting plain-English photo requests into filters with the model chosen for search.translate."""
    from towpath import decisions
    from towpath.mail.threads import ready
    from towpath.models import gateway

    if not store_path(config.store_dir, "queue").exists():
        return []
    import sqlite3

    try:
        with closing(open_store(config.store_dir, "queue", "worker")) as q:
            asked = [dict(r) for r in q.execute("SELECT * FROM photo_questions ORDER BY seq")]
    except sqlite3.OperationalError:
        return []
    with closing(open_store(config.store_dir, "derived", "worker")) as d:
        done = {r[0] for r in d.execute("SELECT seq FROM photo_answers")}
    todo = [a for a in asked if a["seq"] not in done]
    endpoint = config.tasks.get(TASK)
    if not todo or not endpoint or not decisions.model_granted(config, endpoint, "content", role="worker"):
        return []
    if not ready(config, TASK):
        return []
    known = facets(config.store_dir, "worker")
    lists = ("Cameras: " + "; ".join(c for c, _ in known["cameras"][:40]) + "\nLenses: " +
             "; ".join(lens for lens, _ in known["lenses"][:40]))
    out = 0
    with closing(open_store(config.store_dir, "derived", "worker")) as d:
        for a in todo:
            result = gateway.infer(config, TASK, f"{TRANSLATE}\n\n{lists}\n\nRequest: {a['text']}", "content",
                                   TRANSLATE_SCHEMA, prompt_version=PROMPT, background=False)
            filters = result["output"] if result["status"] == "ok" else {"text": a["text"]}
            d.execute("INSERT OR REPLACE INTO photo_answers VALUES (?,?,?,?)",
                      (a["seq"], json.dumps(filters), result["status"], now()))
            d.commit()
            out += 1
    return [{"step": "photo-questions", "translated": out}]
