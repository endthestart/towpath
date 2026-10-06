"""Provider search expanded into attachment parts never loses results across pages (Gmail and IMAP)."""

import sys
from contextlib import closing
from email.message import EmailMessage
from pathlib import Path

import pytest

from towpath import connect
from towpath.stores import open_store
from towpath.unified import federation
from towpath.unified.contracts import Filters

sys.path.insert(0, str(Path(__file__).parent))
import imap_stub as stub  # noqa: E402
from unified_env import Uni  # noqa: E402


@pytest.fixture
def uni(tmp_path, monkeypatch):
    instance = Uni(tmp_path, monkeypatch)
    try:
        yield instance
    finally:
        instance.close()


def collect(adapter, query: str, limit: int, max_pages: int = 20) -> tuple[list[str], list[dict]]:
    """Every result ref across pages, and each page, following next_cursor to the end."""
    refs, pages, cursor = [], [], None
    for _ in range(max_pages):
        page = adapter.search(Filters.parse(query), cursor, limit)
        assert page.status == "ok" and len(page.results) <= limit
        refs += [str(r.ref) for r in page.results]
        pages.append(page)
        cursor = page.next_cursor
        if cursor is None:
            assert page.more_may_exist is False
            return refs, pages
        assert page.more_may_exist is True
    raise AssertionError("paging did not finish")


# -- Gmail: a stubbed provider over the real local index ---------------------------------------------------


class FakeGmail:
    """Stands in for GmailConnector.search: fixed message IDs, pages of `limit`, numeric tokens."""

    def __init__(self, ids):
        self.ids, self.calls = list(ids), []

    def search(self, query, page_token, limit):
        self.calls.append(page_token)
        start = int(page_token or 0)
        page = self.ids[start:start + limit]
        nxt = str(start + limit) if start + limit < len(self.ids) else None
        return {"ids": page, "next_cursor": nxt, "estimate": len(self.ids)}

    def confirm(self, native):
        return None


def _add_parts(uni, native: str, parts: list[tuple[str, str, str]]):
    with closing(open_store(uni.config.store_dir, "source", "connect")) as db:
        item = db.execute("SELECT item_id FROM items WHERE source_id = 'gmail-fixture' AND native_id = ?",
                          (native,)).fetchone()["item_id"]
        for part_id, filename, mime in parts:
            db.execute("INSERT INTO parts (item_id, part_id, depth, mime_type, filename, size) VALUES (?,?,1,?,?,10)",
                       (item, part_id, mime, filename))
        db.commit()


def _gmail(uni, ids):
    adapter = uni.adapters(True)["gmail-fixture"]
    fake = FakeGmail(ids)
    adapter.connector = lambda: fake
    return adapter, fake


def _gmail_ids(uni) -> dict:
    with closing(open_store(uni.config.store_dir, "source", "web")) as db:
        return {r["subject"]: r["native_id"] for r in db.execute(
            "SELECT subject, native_id FROM items WHERE source_id = 'gmail-fixture'")}


def test_one_message_with_two_matching_parts_and_limit_one_loses_nothing(uni):
    ids = _gmail_ids(uni)
    walk = ids["Photos from the aqueduct walk"]
    _add_parts(uni, walk, [("2", "DSC_0045.NEF", "image/x-nikon-nef")])
    adapter, fake = _gmail(uni, [walk])
    refs, pages = collect(adapter, "aqueduct extension:nef", 1)
    assert refs == [f"gmail-fixture:{walk}#part=1", f"gmail-fixture:{walk}#part=2"]
    assert len(pages) == 2 and fake.calls == [None, None]  # the same provider page, resumed after part 1


@pytest.mark.parametrize("limit", [1, 2, 3, 5])
def test_many_messages_many_parts_and_provider_pages_return_each_part_exactly_once(uni, limit):
    ids = _gmail_ids(uni)
    survey, walk, news = ids["Towpath survey notes"], ids["Photos from the aqueduct walk"], ids[
        "Canal society newsletter"]
    _add_parts(uni, walk, [("2", "DSC_0045.NEF", "image/x-nikon-nef"), ("3", "DSC_0045.xmp", "application/rdf+xml"),
                           ("4", "DSC_0046.nef", "image/x-nikon-nef")])
    _add_parts(uni, survey, [("1", "lock.NEF", "image/x-nikon-nef"), ("2", "notes.pdf", "application/pdf")])
    _add_parts(uni, news, [("1", "basin.NEF", "image/x-nikon-nef")])
    adapter, _ = _gmail(uni, [walk, survey, news])
    refs, _ = collect(adapter, "canal extension:nef", limit)
    expected = [f"gmail-fixture:{walk}#part={p}" for p in ("1", "2", "4")] + \
        [f"gmail-fixture:{survey}#part=1", f"gmail-fixture:{news}#part=1"]
    assert refs == expected  # provider order, part order, typed filter (no .xmp or .pdf), no duplicates


def test_messages_without_typed_filters_still_page_by_provider_cursor(uni):
    ids = list(_gmail_ids(uni).values())
    adapter, fake = _gmail(uni, ids)
    refs, _ = collect(adapter, "canal", 1)
    assert refs == [f"gmail-fixture:{i}" for i in ids] and fake.calls == [None, "1", "2", "3"]


def test_a_resumed_page_whose_provider_answer_changed_stops_instead_of_skipping(uni):
    ids = _gmail_ids(uni)
    walk = ids["Photos from the aqueduct walk"]
    _add_parts(uni, walk, [("2", "DSC_0045.NEF", "image/x-nikon-nef")])
    adapter, fake = _gmail(uni, [walk])
    first = adapter.search(Filters.parse("aqueduct extension:nef"), None, 1)
    fake.ids = [ids["Canal society newsletter"]]  # the provider now answers the same page differently
    response = federation.search({"gmail-fixture": adapter}, Filters.parse("aqueduct extension:nef"), 1,
                                 first and _combined(first)).to_dict()
    assert response["sources"][0]["status"] == "error" and response["sources"][0]["error"]["code"] == "stale"


def _combined(page):
    from towpath.unified.contracts import encode_cursor

    return encode_cursor({page.source_id: page.next_cursor})


# -- IMAP: the real connector over the synthetic protocol server --------------------------------------------


def _multi(n: int, subject: str, attachments: list[tuple[str, str]]) -> bytes:
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = "photos@example.com", "reader@example.com", subject
    m["Date"], m["Message-ID"] = "Sun, 22 Aug 2011 10:00:00 +0000", f"<multi-{n}@example.org>"
    m.set_content("More raw files from the canal walk.")
    for name, mime in attachments:
        maintype, subtype = mime.split("/", 1)
        m.add_attachment(b"synthetic " + name.encode(), maintype=maintype, subtype=subtype, filename=name)
    return m.as_bytes()


@pytest.mark.parametrize("limit", [1, 2, 4])
def test_imap_parts_page_through_the_real_protocol_path(uni, limit):
    inbox = uni.state.mailboxes["INBOX"]
    inbox.messages[3] = stub.Message(_multi(3, "Walk raws one", [("A1.NEF", "image/x-nikon-nef"),
                                                                  ("A2.NEF", "image/x-nikon-nef"),
                                                                  ("A2.xmp", "application/rdf+xml")]))
    inbox.messages[4] = stub.Message(_multi(4, "Walk raws two", [("B1.nef", "image/x-nikon-nef")]))
    assert connect.sync(uni.config, "imap-fixture")["termination"] == "complete"
    adapter = uni.adapters(True)["imap-fixture"]
    try:
        refs, _ = collect(adapter, "walk extension:nef", limit)
    finally:
        adapter.close()
    names = {"INBOX;UIDVALIDITY=1700000001;UID=4#part=2": "B1.nef",
             "INBOX;UIDVALIDITY=1700000001;UID=3#part=2": "A1.NEF",
             "INBOX;UIDVALIDITY=1700000001;UID=3#part=3": "A2.NEF",
             "INBOX;UIDVALIDITY=1700000001;UID=2#part=2": "DSC_0044.NEF"}
    assert refs == [f"imap-fixture:{n}" for n in names]  # newest UID first, parts in order, no .xmp
    assert uni.state.violations == []
