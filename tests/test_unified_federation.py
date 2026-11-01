"""One search across sources, wrapping existing records, and the demo command."""

import json

import pytest
from typer.testing import CliRunner

from towpath.cli import app
from towpath.discovery.records import DateFact, Extraction, Locator, Member, Occurrence
from towpath.unified import envelopes, federation, fixtures
from towpath.unified.contracts import (
    ContractError,
    CoverageSummary,
    Filters,
    Match,
    Reference,
    Result,
    SourcePage,
    completeness,
    decode_cursor,
    imap_native,
)


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


def test_folder_size_and_modified_filters_parse():
    f = Filters.parse('in:"photos/2019 trip" size:10MB..2GB modified:2019-02 canal')
    assert f.within == "photos/2019 trip" and (f.min_size, f.max_size) == (10_000_000, 2_000_000_000)
    assert (f.after, f.before) == ("2019-02-01", "2019-02-28") and "file-modified" in f.date_meanings
    assert f.text == "canal"
    assert Filters.parse("size:>500KB").min_size == 500_000 and Filters.parse("size:<1k").max_size == 1000
    assert Filters.parse("in:photos").metadata_only
    assert Filters.from_dict(f.to_dict()) == f
    with pytest.raises(ContractError):
        Filters.parse("size:lots")
    with pytest.raises(ContractError):
        Filters.parse("modified:2019-13")
