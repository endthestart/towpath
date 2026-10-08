"""Plain-language presentation of search responses. Invented data only."""

from datetime import datetime, timedelta, timezone

from towpath.web import present


def status(sid, source_type, caps=None, counts=None, state="ready", freshness=None):
    return {"source_id": sid, "source_type": source_type, "capabilities": caps or {"catalog-search": "verified"},
            "coverage": {"counts": counts or {}}, "state": state, "freshness": freshness or {}, "reason": None}


def test_sources_get_friendly_names_and_ids_only_when_types_repeat():
    statuses = [status("mail", "gmail"), status("archive", "files"), status("photos", "files")]
    assert present.source_labels(statuses) == {"mail": "Gmail", "archive": "Files (archive)",
                                               "photos": "Files (photos)"}


def test_only_sources_the_connector_can_search_inside_are_offered():
    statuses = [status("mail", "gmail", {"provider-search": "disabled"}),
                status("archive", "files", {"content-search": "disabled"}), status("inventory", "files")]
    assert present.deep_sources(statuses, []) == ["mail", "archive"]
    assert present.deep_sources(statuses, ["inventory"]) == []


def test_rows_lead_with_sender_or_attachment_and_drop_internal_jargon():
    message = {"kind": "mail-message", "title": "Canal walk", "match": {"fields": ["subject", "from"]},
               "availability": "present", "dates": [{"value": "2026-03-01T10:00:00+00:00"}],
               "legacy": {"from_addr": "Avery Example <avery@example.com>"}, "coverage": "metadata-cataloged"}
    row = present.row(message, "/ref/?r=x")
    assert (row["lead"], row["title"], row["date"], row["tags"]) == (
        "Avery Example", "Canal walk", "2026-03-01", ["matched sender"])
    part = {"kind": "mail-part", "title": "map.pdf", "match": {"fields": ["filename"]}, "availability": "absent",
            "dates": [], "legacy": {"item": {"subject": None, "from_addr": "lock@example.org"}}}
    row = present.row(part, "/ref/?r=y")
    assert row["lead"] == "Attachment" and row["context"] == "In “(no subject)” from lock@example.org"
    assert row["tags"] == ["matched attachment name", "no longer in the source"] and row["date"] is None
    file = {"kind": "file", "title": None, "extension": "nef", "match": {"fields": []}, "availability": "present",
            "dates": [], "locator": {"path": "DCIM/DSC_0001.NEF"}}
    row = present.row(file, "/ref/?r=z")
    assert (row["lead"], row["title"], row["context"]) == ("NEF file", "(untitled)", "DCIM/DSC_0001.NEF")
    assert present.row({**file, "size": 25_400_000}, "/ref/?r=z")["lead"] == "NEF file · 24.2 MB"
    assert [present.size_text(n) for n in (0, 1023, 1024, 5 * 1024 ** 4, None)] == [
        "0 bytes", "1,023 bytes", "1.0 KB", "5.0 TB", None]


def test_groups_summarise_counts_scope_and_paging():
    page = {"source_id": "mail", "status": "ok", "depth": "catalog", "more_may_exist": True}
    group = present.group(page, [{}] * 20, "Gmail", "gmail")
    assert group["summary"] == "20 results on this page · more available" and group["more"]
    assert group["scope"] == "subjects, senders and attachment names"
    one = present.group({**page, "depth": "provider-search", "more_may_exist": False}, [{}], "Gmail", "gmail")
    assert (one["summary"], one["scope"], one["more"]) == ("1 result", "full message text", False)
    assert present.group({**page, "more_may_exist": False}, [], "Gmail", "gmail")["summary"] == "no matches"


def test_every_reason_and_note_is_kept_with_friendly_names():
    response = {"incomplete_because": ["mail: more results may exist", "something general"],
                "sources": [{"source_id": "mail", "notes": ["provider search: not verified"]}]}
    assert present.reasons(response, {"mail": "Gmail"}) == [
        "Gmail: more results may exist", "something general", "Gmail: provider search: not verified"]


def test_source_names_read_as_a_list():
    assert [present.join(n) for n in ([], ["Gmail"], ["Gmail", "IMAP mail"], ["A", "B", "C"])] == [
        "", "Gmail", "Gmail and IMAP mail", "A, B and C"]


def test_relative_times_and_source_overview():
    now = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
    assert present.ago((now - timedelta(seconds=30)).isoformat(), now) == "just now"
    assert present.ago((now - timedelta(minutes=5)).isoformat(), now) == "5 minutes ago"
    assert present.ago((now - timedelta(hours=1)).isoformat(), now) == "1 hour ago"
    assert present.ago((now - timedelta(days=3)).isoformat(), now) == "3 days ago"
    gmail = status("mail", "gmail", counts={"metadata-cataloged": 4321},
                   freshness={"last_success": "2026-05-04T08:00:00+00:00"})
    assert present.overview(gmail, "Gmail")["line"] == "4,321 messages indexed · last updated 2026-05-04"
