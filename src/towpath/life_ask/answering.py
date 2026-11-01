"""Answering waiting questions through the model, with every sentence cited."""

import json
import time
from contextlib import closing

from towpath.life_ask.asking import INSTRUCTION, PROMPT, SCHEMA, TASK, WRITER, now, questions
from towpath.life_ask.gathering import evidence
from towpath.stores import open_store


def _targets(sources: list[dict]) -> list[str]:
    """What the sources name, for the gateway's checks: messages and their senders (kept from cloud models, or
    excluded from model use, by the owner)."""
    from towpath.mail.privacy import targets

    out: list[str] = list({t for s in sources for t in s.get("targets", [])})
    for s in sources:
        if s.get("source_id") and s.get("item_id"):
            out += targets(s["source_id"], [s["item_id"]], [s.get("from")])
    return sorted(set(out))


def _sources_text(sources: list[dict]) -> str:
    lines = []
    for i, s in enumerate(sources, start=1):
        if s["kind"] == "event":
            quote = f' — quote: "{s["quote"]}"' if s["quote"] else ""
            lines.append(f"[{i}] Event ({s['date']}): {s['text']}{quote}")
        elif s["kind"] == "file":
            lines.append(f"[{i}] File ({s['date'] or 'undated'}): {s['text']}")
        elif s["kind"] == "year":
            lines.append(f"[{i}] The year {s['date']} in Towpath's records: {s['text']}")
        elif s["kind"] == "purchase":
            lines.append(f"[{i}] Purchase ({s['date']}): {s['text']}")
        elif s["kind"] == "attachment":
            lines.append(f"[{i}] Attachment ({s['date']}, from {s['from']}): {s['text']}")
        else:
            lines.append(f"[{i}] Email ({s['date']}, from {s['from']}): {s['text']}")
    return "\n".join(lines)


def answer_pending(config, slice_seconds: float = 120, clock=time.monotonic) -> list[dict]:
    from towpath import decisions as owner_decisions
    from towpath.mail import privacy
    from towpath.mail.threads import _failure, item_refused, ready
    from towpath.models import gateway

    pending = [q for q in questions(config.store_dir, role=WRITER) if q["answer"] is None]
    if not pending:
        return []
    endpoint = config.tasks.get(TASK)
    use_model = bool(endpoint) and owner_decisions.model_granted(config, endpoint, "content", role=WRITER)
    if use_model and not ready(config, TASK):
        return []
    deadline = clock() + slice_seconds
    done = 0
    with closing(open_store(config.store_dir, "life", WRITER)) as db:
        for q in reversed(pending):
            if clock() >= deadline:
                break
            sources = evidence(config, q["text"])
            body = {"sources": sources, "sentences": [], "unknown": not sources}
            status, model = "sources-only", None
            result = {"status": "skipped"}
            for _ in range(2):  # a second time without sources kept from this model, if it refused for them
                if not (use_model and sources):
                    break
                result = gateway.infer(config, TASK, f"{INSTRUCTION}\n\nQuestion: {q['text']}\n\n<sources>\n"
                                       f"{_sources_text(sources)}\n</sources>", "content", SCHEMA,
                                       _targets(sources), prompt_version=PROMPT, background=False)
                if not item_refused(result):
                    break
                kept = privacy.kept(config.store_dir, _targets(sources), role=WRITER)
                sources = [s for s in sources if not set(_targets([s])) & kept]
                body = {"sources": sources, "sentences": [], "unknown": not sources}
            if result["status"] != "skipped":
                if result["status"] == "ok":
                    out = result["output"]
                    body["unknown"] = out["unknown"]
                    body["sentences"] = [{"text": s["text"].strip(),
                                          "sources": sorted({n for n in s["sources"] if 1 <= n <= len(sources)})}
                                         for s in out["sentences"] if s["text"].strip()]
                    status, model = "answered", config.endpoints[endpoint].model
                elif _failure(result):
                    break  # try again on a later poll
            db.execute("INSERT OR REPLACE INTO answers VALUES (?,?,?,?,?)",
                       (q["question_id"], status, json.dumps(body), model, now()))
            db.commit()
            done += 1
    return [{"step": "ask", "answered": done}] if done else []
