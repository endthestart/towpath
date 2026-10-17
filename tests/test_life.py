"""The life story's foundation: proposed claims are stored as they were proposed, and the owner's reviews survive a
re-run. Invented content only."""

from contextlib import closing

import pytest

from towpath import life
from towpath.stores import open_store
from towpath.unified.claims import Claim, Citation, DateBasis, When


def proposal(statement: str, day: str | None, ref: str = "gmail-x:msg1", modality: str = "plan") -> Claim:
    when = (When(day, "day", day, day, (DateBasis(0, "message-date"),)) if day
            else When("unknown", "unknown"))
    citation = Citation(ref=ref, captured_excerpt="Your booking is confirmed.",
                        date_facts=({"meaning": "message-date", "value": f"{day or '2020-01-01'}T09:00:00+00:00"},))
    return Claim(life.claim_id(statement, modality, [ref]), "event", statement, modality, when, (citation,),
                 "test/proposer@0")


@pytest.fixture
def store(tmp_path):
    from towpath.unified import grants

    grants.grant(tmp_path, "gmail-x", "life-evidence", author="owner")  # events only come from allowed accounts
    db = open_store(tmp_path, "life", life.WRITER)
    yield tmp_path, db
    db.close()


def test_a_claim_is_stored_exactly_as_proposed_and_only_once(store):
    store_dir, db = store
    claim = proposal("Planned a trip to Example Junction", "2019-05-14")
    assert claim.claim_id == life.claim_id("planned a  trip to Example Junction", "plan", ["gmail-x:msg1"])  # stable
    assert life.save(db, claim, "gmail-x", "run1") is True
    assert life.save(db, claim, "gmail-x", "run2") is False  # proposed again: same ID, nothing duplicated
    db.commit()
    assert life.load(db, claim.claim_id).canonical() == claim.canonical()


def test_claims_come_in_date_order_with_undated_ones_last(store):
    store_dir, db = store
    for statement, day in (("Moved house", "2015-03-01"), ("Started a new job", "2012-09-03"), ("Undated note", None)):
        life.save(db, proposal(statement, day, ref=f"gmail-x:{statement[:5]}"), "gmail-x", "run1")
    db.commit()
    assert [c.statement for c in life.claims(store_dir)] == ["Started a new job", "Moved house", "Undated note"]
    assert [c.statement for c in life.claims(store_dir, after="2014-01-01", before="2016-01-01")] == [
        "Moved house", "Undated note"]


def test_the_owners_review_survives_proposing_the_same_claim_again(store):
    store_dir, db = store
    trip = proposal("Planned a trip to Example Junction", "2019-05-14")
    job = proposal("Started a new job", "2012-09-03", ref="gmail-x:msg2", modality="occurred")
    for claim in (trip, job):
        life.save(db, claim, "gmail-x", "run1")
    db.commit()
    life.review(store_dir, trip.claim_id, "accept")
    life.review(store_dir, job.claim_id, "correct", correction={
        "statement": "Started at the Example Lock Company",
        "when": {"expression": "September 2012", "precision": "month", "earliest": "2012-09-01",
                 "latest": "2012-09-30"}})
    life.save(db, proposal("Planned a trip to Example Junction", "2019-05-14"), "gmail-x", "run2")  # a re-run
    db.commit()
    shown = {c.claim_id: c for c in life.claims(store_dir)}
    assert shown[trip.claim_id].state == "accepted" and shown[trip.claim_id].review["by"] == "owner"
    corrected = shown[job.claim_id]
    assert (corrected.state, corrected.statement, corrected.when.precision) == (
        "corrected", "Started at the Example Lock Company", "month")
    assert corrected.when.basis == job.when.basis  # still dated by the cited message
    life.review(store_dir, job.claim_id, "reopen")
    assert {c.claim_id: c for c in life.claims(store_dir)}[job.claim_id].statement == "Started a new job"
    with closing(open_store(store_dir, "decisions", "web")) as decisions:
        kinds = [r[0] for r in decisions.execute("SELECT kind FROM decision_log ORDER BY seq")]
    assert [k for k in kinds if k != "source-grant"] == ["claim-review"] * 3


def test_a_review_that_changes_nothing_or_breaks_the_date_is_refused(store):
    store_dir, db = store
    claim = proposal("Planned a trip", "2019-05-14")
    life.save(db, claim, "gmail-x", "run1")
    db.commit()
    with pytest.raises(ValueError):
        life.review(store_dir, claim.claim_id, "correct", correction={"statement": ""})
    with pytest.raises(ValueError):
        life.review(store_dir, claim.claim_id, "correct", correction={"when": {"expression": "soon"}})
    with pytest.raises(ValueError):
        life.review(store_dir, claim.claim_id, "delete")


def test_no_life_store_yet_means_no_claims(tmp_path):
    assert life.claims(tmp_path) == []
