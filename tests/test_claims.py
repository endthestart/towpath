"""Timeline claims and typed dates: validation, precision, process-date refusal, contradictions, round trips."""

import copy
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from towpath.cli import app
from towpath.unified import claims
from towpath.unified.claims import Claim, ClaimError, When

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "claims.example.json"


def examples() -> list[dict]:
    return json.loads(EXAMPLE.read_text())


def test_every_example_claim_validates_and_round_trips_exactly():
    for document in examples():
        assert claims.validate(document) == [], document["claim_id"]
        claim = Claim.from_dict(document)
        again = Claim.from_dict(json.loads(json.dumps(claim.to_dict())))
        assert again == claim and again.canonical() == claim.canonical()
        assert again.when.precision == document["when"]["precision"]
        assert again.when.expression == document["when"]["expression"]


def test_the_interfaces_draft_example_shape_is_accepted():
    draft = {
        "schema": "towpath.claim/0", "claim_id": "clm_0007", "kind": "event",
        "statement": "Planned a trip to Example City", "modality": "plan",
        "when": {"expression": "mid-May 2026", "earliest": "2026-05-10", "latest": "2026-05-20", "precision": "range"},
        "citations": [
            {"occurrence_id": "occ_0055", "part": "text/plain", "span": [120, 188], "excerpt_sha256": "7" * 64,
             "captured_excerpt": "Your booking for Example City, 14-17 May, is confirmed.",
             "preserved": {"level": "item", "artifact_sha256": "a" * 64}},
            {"external": {"source_id": "src_photos", "kind": "photo-library", "native_id": "asset-7f3e"},
             "observed": "taken 2026-05-15, Example City"}],
        "audience": "owner", "model_use": "follow-grants", "producer": "rules/travel-confirmation@0",
        "state": "proposed", "uncertainty": "A booking confirmation supports a plan, not that travel occurred."}
    assert claims.validate(draft) == []
    assert Claim.from_dict(draft).to_dict()["citations"][0]["preserved"]["level"] == "item"


@pytest.mark.parametrize("when,message", [
    ({"expression": "15 May", "earliest": "2026-05-15", "latest": "2026-05-16", "precision": "day"}, "same earliest"),
    ({"expression": "May", "earliest": "2026-05-02", "latest": "2026-05-31", "precision": "month"}, "first to its"),
    ({"expression": "Feb", "earliest": "2024-02-01", "latest": "2024-02-28", "precision": "month"}, "first to its"),
    ({"expression": "2003", "earliest": "2003-01-01", "latest": "2003-06-30", "precision": "year"}, "January 1"),
    ({"expression": "x", "earliest": "2026-05-20", "latest": "2026-05-10", "precision": "range"}, "after latest"),
    ({"expression": "x", "earliest": "2026-05-10", "latest": "2026-05-10", "precision": "range"}, "use precision day"),
    ({"expression": "x", "earliest": "2026-05-10", "precision": "range"}, "needs earliest and latest"),
    ({"expression": "noon", "earliest": "2026-05-10T12:00:00Z", "latest": "2026-05-10T13:00:00Z",
      "precision": "instant"}, "identical"),
    ({"expression": "someday", "earliest": "2026-01-01", "latest": "2026-12-31", "precision": "unknown"}, "no bounds"),
    ({"expression": "", "precision": "unknown"}, "cannot be empty"),
    ({"expression": "x", "earliest": "2026-05-10T00:00:00Z", "latest": "2026-05-11T00:00:00Z",
      "precision": "range"}, "calendar days"),
])
def test_dates_keep_their_stated_precision(when, message):
    with pytest.raises(ClaimError, match=message):
        When.from_dict(when)


def test_leap_february_and_instants_are_exact():
    assert When.from_dict({"expression": "Feb 2024", "earliest": "2024-02-01", "latest": "2024-02-29",
                           "precision": "month"}).bounds()[1].day == 29
    instant = When.from_dict({"expression": "at noon", "earliest": "2026-05-10T12:00:00+02:00",
                              "latest": "2026-05-10T12:00:00+02:00", "precision": "instant"})
    assert instant.to_dict()["earliest"] == "2026-05-10T12:00:00+02:00"  # the offset is kept as stated


def test_recovery_and_other_process_dates_never_date_an_event():
    document = copy.deepcopy(examples()[3])  # the paper: cites a message date and a recovery date
    document["modality"] = "occurred"
    document["when"] = {"expression": "2026", "earliest": "2026-01-01", "latest": "2026-12-31", "precision": "year",
                        "basis": [{"citation": 0, "meaning": "recovered-at"}]}
    assert any("process dates" in p for p in claims.validate(document))
    document["when"]["basis"] = [{"citation": 0, "meaning": "message-date"}]
    document["when"].update(expression="2003", earliest="2003-01-01", latest="2003-12-31")
    assert claims.validate(document) == []


def test_basis_must_point_at_a_citation_that_carries_that_date():
    document = copy.deepcopy(examples()[1])
    document["when"]["basis"] = [{"citation": 3, "meaning": "file-modified"}]
    assert any("does not exist" in p for p in claims.validate(document))
    document["when"]["basis"] = [{"citation": 0, "meaning": "message-date"}]
    assert any("does not carry" in p for p in claims.validate(document))


@pytest.mark.parametrize("change,message", [
    (lambda d: d.update(modality="dreamed"), "modality"),
    (lambda d: d.update(schema="towpath.claim/9"), "unsupported claim schema"),
    (lambda d: d.update(confidence=0.9), "unknown claim field"),
    (lambda d: d.update(citations=[]), "only a recollection"),
    (lambda d: d.update(state="accepted", review=None), "who reviewed"),
    (lambda d: d.update(relations=[{"type": "agrees", "claim_id": "clm_x"}]), "relations"),
    (lambda d: d["citations"][0].update(ref="no-colon"), "is not <source>:<native>"),
    (lambda d: d["citations"][0].update(external={"source_id": "x", "native_id": "y"}), "exactly one"),
    (lambda d: d.update(claim_id="trip"), "claim_id"),
])
def test_invalid_claims_are_rejected_with_a_reason(change, message):
    document = copy.deepcopy(examples()[0])
    change(document)
    problems = claims.validate(document)
    assert problems and message in problems[0]


def test_timeline_orders_by_date_keeps_modalities_and_surfaces_contradictions():
    parsed = [Claim.from_dict(d) for d in examples()]
    view = claims.timeline(parsed)
    assert [e["claim_id"] for e in view["entries"]] == ["clm_thesis_year", "clm_trip_plan", "clm_trip_photos",
                                                         "clm_trip_memory"]
    assert {e["claim_id"]: e["modality"] for e in view["entries"]}["clm_trip_plan"] == "plan"
    conflicts = {tuple(c["claims"]): c["why"] for c in view["conflicts"]}
    assert conflicts == {("clm_trip_memory", "clm_trip_photos"): "marked as contradicting"}
    moved = copy.deepcopy(examples())
    moved[1]["when"].update(expression="2026-07-01", earliest="2026-07-01", latest="2026-07-01")
    later = claims.timeline([Claim.from_dict(d) for d in moved])
    assert ("clm_trip_photos", "clm_trip_plan") in {tuple(c["claims"]) for c in later["conflicts"]}
    assert len(later["entries"]) == 4  # nothing merged, nothing dropped


def test_claims_commands(tmp_path):
    runner = CliRunner()
    good = runner.invoke(app, ["claims", "validate", str(EXAMPLE)])
    assert good.exit_code == 0 and json.loads(good.output)["valid"] is True
    bad = tmp_path / "bad.jsonl"
    broken = copy.deepcopy(examples()[1])
    broken["when"]["basis"] = [{"citation": 0, "meaning": "indexed-at"}]
    broken["citations"][0]["date_facts"].append({"meaning": "indexed-at", "value": "2026-10-01T00:00:00Z"})
    bad.write_text(json.dumps(examples()[0]) + "\n" + json.dumps(broken) + "\n")
    out = runner.invoke(app, ["claims", "validate", str(bad)])
    assert out.exit_code == 1
    report = json.loads(out.output)["claims"]
    assert report[0]["problems"] == [] and "process dates" in report[1]["problems"][0]
    timeline = runner.invoke(app, ["claims", "timeline", str(EXAMPLE)])
    assert timeline.exit_code == 0 and len(json.loads(timeline.output)["conflicts"]) == 1
