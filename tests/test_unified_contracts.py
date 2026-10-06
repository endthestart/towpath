"""Unified contracts: references, typed dates and filters, result envelopes, per-source pages, federation."""

import json

import pytest
from typer.testing import CliRunner

from towpath.cli import app
from towpath.discovery.records import DateFact, Extraction, Locator, Member, Occurrence
from towpath.unified import envelopes, federation, fixtures
from towpath.unified.contracts import (
    Citation,
    ContractError,
    CoverageSummary,
    Excerpt,
    Filters,
    Match,
    Reference,
    Result,
    SourcePage,
    SourceStatus,
    TypedDate,
    completeness,
    decode_cursor,
    encode_cursor,
    imap_native,
    parse_imap_native,
    part_native,
    split_part,
)

# -- references -------------------------------------------------------------------------------------


@pytest.mark.parametrize("mailbox", ["INBOX", "Archive/2008", "Notes; with #marks", "Entwürfe", "50% done"])
def test_imap_identity_round_trips_every_mailbox_name(mailbox):
    native = imap_native(mailbox, 1700000001, 42)
    assert parse_imap_native(native) == (mailbox, 1700000001, 42)
    ref = Reference.parse(str(Reference("imap-a", native)))
    assert ref.source_id == "imap-a" and parse_imap_native(ref.native) == (mailbox, 1700000001, 42)
    message, part = split_part(part_native(native, "1.2"))
    assert (message, part) == (native, "1.2")
    assert split_part(native) == (native, None)


@pytest.mark.parametrize("bad", ["INBOX;UID=4", "INBOX;UIDVALIDITY=0;UID=4", "INBOX;UIDVALIDITY=9;UID=0", ""])
def test_imap_identity_requires_mailbox_uidvalidity_and_uid(bad):
    with pytest.raises(ContractError):
        parse_imap_native(bad)
    with pytest.raises(ContractError):
        imap_native("INBOX", 0, 1)


@pytest.mark.parametrize("text", ["no-colon", "Upper:x", ":native", "src:"])
def test_references_reject_malformed_text(text):
    with pytest.raises(ContractError):
        Reference.parse(text)


def test_part_ids_are_mime_part_identifiers():
    with pytest.raises(ContractError):
        part_native("fx-1", "../1")


# -- dates and filters --------------------------------------------------------------------------------


def test_typed_dates_keep_meaning_precision_and_process_flag():
    d = TypedDate("message-date", "2009-04-02T10:15:00+00:00")
    assert not d.is_process_date and d.day() == "2009-04-02"
    assert TypedDate("indexed-at", "2026-10-06T00:00:00Z").is_process_date
    assert TypedDate.from_dict(TypedDate("captured", "2011-08", "unknown").to_dict()).precision == "unknown"
    with pytest.raises(ContractError):
        TypedDate("happened", "2009-04-02")
    with pytest.raises(ContractError):
        TypedDate("message-date", "last spring")


def test_filter_syntax_separates_typed_filters_from_keywords():
    f = Filters.parse("extension:.NEF kind:file after:2010-01-01 before:2012-12-31 source:files-a aqueduct walk")
    assert f.extensions == ("nef",) and f.kinds == ("file",) and f.sources == ("files-a",)
    assert (f.after, f.before) == ("2010-01-01", "2012-12-31") and f.text == "aqueduct walk"
    assert Filters.parse("extension:nef").metadata_only
    assert Filters.from_dict(f.to_dict()) == f
    assert Filters.parse("aqueduct", extensions=["nef"]).extensions == ("nef",)


@pytest.mark.parametrize("query", ["", "   ", "extension:ne.f", "kind:folder x", "after:yesterday x",
                                   "source:Bad x"])
def test_invalid_filters_are_rejected(query):
    with pytest.raises(ContractError):
        Filters.parse(query)


def test_unknown_filter_fields_are_rejected():
    with pytest.raises(ContractError):
        Filters.from_dict({"text": "x", "everything": True})


def _file(dates, ext="nef", kind="file"):
    return Result("files-a", "files", Reference("files-a", "occ_1"), kind, f"x.{ext}", Match("catalog"),
                  dates=tuple(dates), extension=ext, media_type="image/x-nikon-nef")


def test_date_filters_use_evidence_dates_never_process_dates():
    indexed_only = _file([TypedDate("indexed-at", "2011-05-01T00:00:00Z")])
    modified = _file([TypedDate("file-modified", "2011-05-01T00:00:00Z")])
    f = Filters.parse("after:2011-01-01 before:2011-12-31 extension:nef")
    assert not f.accepts_metadata(indexed_only)
    assert f.accepts_metadata(modified)
    assert not Filters.parse("after:2012-01-01 extension:nef").accepts_metadata(modified)
    assert Filters.parse("type:image/ extension:nef").accepts_metadata(modified)
    assert not Filters.parse("kind:mail-message extension:nef").accepts_metadata(modified)


# -- results, citations, coverage, pages ----------------------------------------------------------------


def test_a_provider_match_never_claims_a_verified_passage():
    with pytest.raises(ContractError):
        Match("provider-search", ("body",), verified_passage=True)
    with pytest.raises(ContractError):
        Match("full-text")


def test_results_must_name_their_own_source_and_known_states():
    with pytest.raises(ContractError):
        Result("files-a", "files", Reference("files-b", "occ_1"), "file", "x", Match("catalog"))
    with pytest.raises(ContractError):
        Result("files-a", "files", Reference("files-a", "occ_1"), "file", "x", Match("catalog"), coverage="done")


def test_excerpts_are_bounded_and_labelled_untrusted():
    cite = Citation("files-a:occ_1", "v1", {"kind": "text-offset", "start": 0, "length": 5}, "a" * 64)
    ex = Excerpt("hello", cite, False, 5)
    assert ex.to_dict()["content_is_untrusted_data"] is True
    with pytest.raises(ContractError):
        Excerpt("hello!", cite, False, 5)
    assert Citation.from_dict(cite.to_dict()) == cite
    with pytest.raises(ContractError):
        Citation("files-a:occ_1", "v1", {"start": 0})


def test_file_citations_wrap_without_change():
    legacy = {"citation_id": "cit_1", "occurrence_id": "occ_9", "version": "size=1;mtime=2",
              "location": {"kind": "text-offset", "start": 3, "length": 4}, "excerpt_sha256": "b" * 64,
              "source": {"size": 1, "mtime": 2}, "created_at": "2026-10-06T00:00:00+00:00"}
    cite = Citation.from_file_citation("files-a", legacy)
    assert cite.ref == "files-a:occ_9" and cite.location == legacy["location"] and cite.source_stamp == legacy["source"]


def test_content_coverage_cannot_outrun_the_inventory():
    with pytest.raises(ContractError):
        CoverageSummary({"text-extracted": 3}, inventory_complete=None, content_complete=True)
    with pytest.raises(ContractError):
        CoverageSummary({"indexed": 3})


def test_failed_pages_carry_no_results_and_pages_hold_one_source():
    r = _file([])
    with pytest.raises(ContractError):
        SourcePage("files-a", "unavailable", results=(r,))
    with pytest.raises(ContractError):
        SourcePage("files-b", "ok", "catalog", (r,))


def test_status_rejects_unknown_capabilities_and_depths():
    with pytest.raises(ContractError):
        SourceStatus("x", "files", "X", "ready", {"teleport": "verified"}, ("catalog",))
    with pytest.raises(ContractError):
        SourceStatus("x", "files", "X", "ready", {}, ("semantic",))


def test_cursor_round_trip_and_tampering():
    token = encode_cursor({"files-a": "20", "gmail-a": "tok/en", "done": None})
    assert decode_cursor(token) == {"files-a": "20", "gmail-a": "tok/en"}
    assert encode_cursor({"x": None}) is None
    for bad in ["not base64 !", "e30", "WyJhIl0", "eyJCQUQiOiAiMSJ9"]:  # {}, ["a"], {"BAD": "1"}
        if bad == "e30":
            assert decode_cursor(bad) == {}
            continue
        with pytest.raises(ContractError):
            decode_cursor(bad)


# -- federation ----------------------------------------------------------------------------------------


def test_one_query_returns_distinctly_attributed_results_from_three_source_types():
    resp = federation.search(fixtures.demo_adapters(), Filters.parse("aqueduct"), limit=10).to_dict()
    by_type = {r["source_type"] for r in resp["results"]}
    assert by_type == {"gmail", "imap", "files"}
    for r in resp["results"]:
        assert r["ref"].startswith(r["source_id"] + ":") and r["match"]["verified_passage"] is False
    depths = {s["source_id"]: s["depth"] for s in resp["sources"]}
    assert depths == {"gmail-fixture": "provider-search", "imap-fixture": "provider-search",
                      "files-fixture": "content-index"}
    assert resp["complete"] is False
    assert any("inventory" in reason for reason in resp["incomplete_because"])
    assert "no combined total" in resp["notes"][0]
    assert "total" not in resp and all("total" not in s for s in resp["sources"])


def test_an_unavailable_source_does_not_erase_the_others():
    resp = federation.search(fixtures.demo_adapters(imap_mode="unavailable"), Filters.parse("aqueduct")).to_dict()
    imap = next(s for s in resp["sources"] if s["source_id"] == "imap-fixture")
    assert imap["status"] == "unavailable" and imap["error"]["code"] == "unavailable" and imap["result_count"] == 0
    assert {r["source_type"] for r in resp["results"]} == {"gmail", "files"}
    assert "imap-fixture: unavailable (unavailable)" in resp["incomplete_because"]


def test_nef_query_returns_type_references_without_text_and_no_all_found_claim():
    resp = federation.search(fixtures.demo_adapters(), Filters.parse("extension:nef")).to_dict()
    refs = {r["ref"]: r for r in resp["results"]}
    assert set(refs) == {"files-fixture:occ_fx_nef_0001", "files-fixture:occ_fx_nef_0002",
                         "gmail-fixture:fx-g-0002#part=1"}
    assert all(r["excerpt"] is None and r["extension"] == "nef" for r in refs.values())
    assert refs["files-fixture:occ_fx_nef_0001"]["coverage"] == "unsupported"
    assert all(s["depth"] == "catalog" for s in resp["sources"] if s["status"] in {"ok", "partial"})
    assert resp["complete"] is False  # files inventory is partial


def test_complete_only_when_every_source_finished_with_a_complete_inventory():
    adapters = fixtures.demo_adapters(files_mode="ok")
    adapters.pop("files-fixture")
    resp = federation.search(adapters, Filters.parse("extension:nef")).to_dict()
    assert resp["complete"] is True and resp["incomplete_because"] == []
    keyword = federation.search(adapters, Filters.parse("aqueduct")).to_dict()
    assert keyword["complete"] is False  # provider search coverage is not Towpath's to vouch for


def test_keyword_text_on_a_catalog_is_labelled_metadata_only():
    page = SourcePage("files-a", "ok", "catalog", more_may_exist=False,
                      coverage=CoverageSummary({}, True, False))
    complete, reasons = completeness(Filters.parse("canal"), [page])
    assert not complete and reasons == ["files-a: keyword text matched metadata only, not message or file content"]


def test_paging_continues_only_sources_with_more():
    adapters = fixtures.demo_adapters(files_mode="ok")
    first = federation.search(adapters, Filters.parse("extension:nef"), limit=1).to_dict()
    assert decode_cursor(first["next_cursor"]) == {"files-fixture": "1"}
    second = federation.search(adapters, Filters.parse("extension:nef"), limit=1, cursor=first["next_cursor"])
    second = second.to_dict()
    assert [s["source_id"] for s in second["sources"]] == ["files-fixture"]
    assert [r["ref"] for r in second["results"]] == ["files-fixture:occ_fx_nef_0002"]
    assert second["next_cursor"] is None
    seen = {r["ref"] for r in first["results"]} | {r["ref"] for r in second["results"]}
    assert len(seen) == 3


class _Broken(federation.SourceAdapter):
    source_id, source_type = "broken", "files"

    def __init__(self, exc=None, results=0):
        self.exc, self.results = exc, results

    def search(self, filters, cursor, limit):
        if self.exc:
            raise self.exc
        r = Result("broken", "files", Reference("broken", "occ_1"), "file", "x", Match("catalog"))
        return SourcePage("broken", "ok", "catalog", tuple([r] * self.results))


def test_unexpected_adapter_errors_report_their_type_only():
    adapters = {**fixtures.demo_adapters(), "broken": _Broken(RuntimeError("secret@example.com said no"))}
    resp = federation.search(adapters, Filters.parse("aqueduct")).to_dict()
    broken = next(s for s in resp["sources"] if s["source_id"] == "broken")
    assert broken["status"] == "error" and "example.com" not in json.dumps(broken)
    assert len(resp["results"]) > 0


def test_adapters_cannot_overfill_a_page_or_name_unknown_sources():
    resp = federation.search({"broken": _Broken(results=3)}, Filters.parse("x"), limit=2).to_dict()
    assert resp["sources"][0]["status"] == "error" and resp["results"] == []
    resp = federation.search({}, Filters.parse("x source:nowhere")).to_dict()
    assert resp["sources"][0]["status"] == "unknown-source"
    with pytest.raises(ContractError):
        federation.search({}, Filters.parse("x"), limit=0)


def test_registry_reports_each_source_state():
    rows = federation.statuses(fixtures.demo_adapters())
    assert [r["source_type"] for r in rows] == ["gmail", "imap", "files"]
    files = rows[2]
    assert files["coverage"]["inventory_complete"] is False and files["coverage"]["counts"]["pending"] == 40
    assert set(files["depths"]) == {"catalog", "content-index"}


def test_describe_dispatches_to_the_reference_source():
    adapters = fixtures.demo_adapters()
    out = federation.describe(adapters, "files-fixture:occ_fx_txt_0003")
    assert out["result"]["locator"]["path"] == "notes/towpath-survey.txt"
    with pytest.raises(federation.UnknownReference):
        federation.describe(adapters, "nowhere:x")


# -- legacy record wrapping -------------------------------------------------------------------------------


def _occurrence(**kw):
    locator = Locator("fixture", "archive", "2003/old-mail.zip",
                      (Member("archive-member", "mail/backup.mbox"), Member("mail-message", index=2),
                       Member("attachment", "paper.docx")), "native-7")
    return Occurrence(locator, Extraction(kw.get("status", "indexed"), "fx", "1", 10, 100), "application/x",
                      4096, (DateFact("member-modified", "2004-01-01T12:00:00+00:00", "zip"),
                             DateFact("indexed-at", "2026-10-06T00:00:00+00:00", "provider")),
                      kw.get("hashes", {}), "v1")


def test_file_occurrences_wrap_with_identity_dates_and_legacy_record():
    occ = _occurrence().to_dict()
    r = envelopes.file_result("files-a", occ, depth="content-index", fields=("text",), provider_rank=1)
    d = r.to_dict()
    assert d["ref"] == f"files-a:{occ['occurrence_id']}" and d["kind"] == "archive-member"
    assert d["title"] == "paper.docx" and d["extension"] == "docx" and d["coverage"] == "text-extracted"
    assert d["locator"]["display"] == "archive:2003/old-mail.zip > mail/backup.mbox > mail-message 2 > paper.docx"
    assert d["hashes"] == {}  # never invented
    assert {x["meaning"]: x["process_date"] for x in d["dates"]} == {"member-modified": False, "indexed-at": True}
    assert d["legacy"] == occ
    unreadable = envelopes.file_result("files-a", _occurrence(status="encrypted").to_dict())
    assert unreadable.coverage == "unreadable"


def test_files_store_rows_wrap_the_same_as_occurrence_records():
    occ = _occurrence(hashes={"sha256": "c" * 64}).to_dict()
    row = {"occurrence_id": occ["occurrence_id"], "provider_id": "fixture", "native_id": "native-7",
           "root_alias": "archive", "rel_path": "2003/old-mail.zip", "members": json.dumps(occ["locator"]["members"]),
           "media_type": "application/x", "size": 4096, "dates": json.dumps(occ["dates"]),
           "hashes": json.dumps(occ["hashes"]), "extraction": json.dumps(occ["extraction"]), "version": "v1",
           "missing_since_run": "run_9"}
    a, b = envelopes.file_result("files-a", occ), envelopes.file_result("files-a", row)
    assert (a.ref, a.title, a.locator["display"], a.hashes) == (b.ref, b.title, b.locator["display"], b.hashes)
    assert b.availability == "missing" and a.availability == "present"


def test_mail_rows_wrap_without_bodies_and_imap_identity_reaches_the_locator():
    native = imap_native("Archive/2008", 1700000002, 3)
    row = {"item_id": "itm_1", "native_id": native, "thread_id": None, "rfc_message_id": "<m1@example.com>",
           "subject": "Canal boat club minutes", "from_addr": "secretary@example.com",
           "date_utc": "2008-06-30T20:00:00+00:00", "internal_date": "1214856000000", "size_estimate": 900,
           "absent_since_run": None}
    r = envelopes.mail_result("imap-a", "imap", row, labels=["Archive/2008"]).to_dict()
    assert r["locator"]["mailbox"] == "Archive/2008" and r["locator"]["uidvalidity"] == 1700000002
    assert r["version"] == "UIDVALIDITY=1700000002" and r["coverage"] == "metadata-cataloged"
    assert [d["meaning"] for d in r["dates"]] == ["message-date", "provider-received"]
    assert "body" not in json.dumps(r).lower().replace("nobody", "")
    part = envelopes.mail_part_result("imap-a", "imap", row, {"part_id": "2", "mime_type": "image/x-nikon-nef",
                                                              "filename": "DSC_0001.NEF", "size": 10})
    assert part.ref.native == native + "#part=2" and part.extension == "nef" and part.kind == "mail-part"
    gone = envelopes.mail_result("gmail-a", "gmail", {**row, "native_id": "fx1", "absent_since_run": "run_2"})
    assert gone.availability == "absent"


# -- command line --------------------------------------------------------------------------------------------


def test_demo_command_is_synthetic_and_reports_states():
    out = CliRunner().invoke(app, ["search", "demo", "aqueduct", "--imap-mode", "unavailable"])
    assert out.exit_code == 0, out.output
    data = json.loads(out.output)
    assert data["synthetic"] is True and data["schema"] == "towpath.search/0"
    assert {s["source_id"]: s["status"] for s in data["sources"]} == {
        "gmail-fixture": "ok", "imap-fixture": "unavailable", "files-fixture": "partial"}
    bad = CliRunner().invoke(app, ["search", "demo", "extension:n.e.f"])
    assert bad.exit_code == 2 and json.loads(bad.output)["error"]["code"] == "invalid-request"
