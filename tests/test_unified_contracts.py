"""Unified contracts: references, typed dates and filters, result envelopes, per-source pages, federation."""

import pytest

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
    decode_cursor,
    encode_cursor,
    imap_native,
    parse_imap_native,
    part_native,
    split_part,
)


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


@pytest.mark.parametrize("text", ["no-colon", "-dash:x", ":native", "src:", "sp ace:x"])
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
                                   "source:-bad x"])
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
    for bad in ["not base64 !", "e30", "WyJhIl0", "eyJhOmIiOiAiMSJ9"]:  # {}, ["a"], {"a:b": "1"}
        if bad == "e30":
            assert decode_cursor(bad) == {}
            continue
        with pytest.raises(ContractError):
            decode_cursor(bad)
