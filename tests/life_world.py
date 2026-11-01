"""A mailbox with events proposed from it and a model answering by rule, for the life story's tests."""

import json
import re
from contextlib import closing

from towpath import life
from towpath.mail import senders
from towpath.stores import open_store
from towpath.unified import grants
from towpath.unified.claims import Citation, Claim, When

from tests.test_mail_threads import World, answer as thread_answer

OWNER = "correct horse battery staple"


def respond(prompt: str) -> str:
    if "see their life as memories" in prompt:
        ids = re.findall(r"^- (clm_\w+) \|", prompt, re.M)
        trip = [i for i in ids if i in TRIP]
        return json.dumps({"groups": [{"kind": "trip", "title": "Trip to Example Junction", "members": trip,
                                       "summary": "Four days by the canal."}] if len(trip) >= 2 else []})
    if "Answer the owner's question" in prompt:
        return json.dumps({"unknown": False, "sentences": [
            {"text": "You booked Example Junction in May 2009.", "sources": [1]},
            {"text": "It rained the whole time.", "sources": []}]})
    return thread_answer(prompt)


TRIP: set[str] = set()


def claim(statement, day, ref, significance="notable", modality="occurred") -> Claim:
    when = When(day, "day", day, day)
    return Claim(life.claim_id(statement, modality, [ref]), "event", statement, modality, when,
                 (Citation(ref=ref, captured_excerpt=statement),), "test", significance=significance)


def make_world(tmp_path, openai_stub):
    w = World(tmp_path, openai_stub)
    w.model_stub.respond = respond
    senders.categorize_pending(w.worker())
    with closing(open_store(w.store, "source", "web")) as src:
        sam = src.execute("SELECT native_id FROM items WHERE subject = 'Saturday?'").fetchone()[0]
    made = [claim("Booked a trip to Example Junction", "2009-05-01", f"{w.sid}:{sam}"),
            claim("Flew to Example Junction", "2009-05-14", f"{w.sid}:{sam}x"),
            claim("Stayed at the canal hotel", "2009-05-15", f"{w.sid}:{sam}y"),
            claim("Helped Sam move", "2011-03-03", f"{w.sid}:{sam}"),
            claim("Dinner with Sam", "2011-04-02", f"{w.sid}:{sam}"),
            claim("Started a new job", "2015-09-01", f"{w.sid}:z", "milestone")]
    TRIP.clear()
    TRIP.update(c.claim_id for c in made[:3])
    grants.grant(w.store, w.sid, "life-evidence", author="owner")  # events only come from allowed accounts
    with closing(open_store(w.store, "life", "worker")) as db:
        for c in made:
            life.save(db, c, w.sid, "run_test")
        db.commit()
    yield w
    w.server.__exit__(None, None, None)
