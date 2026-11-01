"""Recoll adapter: paging through a listing, and what an unfinished listing can't establish."""

from towpath import config as config_mod
from towpath.discovery import service
from towpath.discovery.providers import recoll as recoll_provider

def _raw_rows(rw, root: str = "archive") -> int:
    base = f"file://{rw.root / 'roots' / root}/"
    return sum(1 for row in rw.index if row.get("url", "").startswith(base))


def test_listing_boundary_is_exact_even_with_filtered_rows(rw):
    rw.grant("archive", "search")
    folder = rw.root / "roots/archive/2003"
    rw.index.append({"url": f"file://{folder}", "ipath": "", "mtype": "inode/directory", "rcludi": f"{folder}|",
                     "text": ""})  # filtered by the adapter, but still a raw row
    rw.save()
    raw = _raw_rows(rw)
    exact = service.import_catalog(rw.config, root="archive", max_items=raw)[0]
    assert exact["complete"] and exact["absence_established"] and exact["occurrences_seen"] == 7
    short = service.import_catalog(rw.config, root="archive", max_items=raw - 1)[0]
    assert (short["complete"], short["termination"], short["marked_missing"]) == (False, "partial", 0)
    assert "max_items" in short["reason"]
    one = service.import_catalog(rw.config, root="archive", max_items=1)[0]
    assert one["occurrences_seen"] <= 1 and one["marked_missing"] == 0


def test_exhausted_listing_after_a_deletion_marks_only_that_record(rw):
    rw.grant("archive", "search")
    service.import_catalog(rw.config, root="archive")
    rw.index = [row for row in rw.index if not row.get("url", "").endswith("notes/injection.md")]
    rw.save()  # as if recollindex ran after the file was deleted
    report = service.import_catalog(rw.config, root="archive", max_items=_raw_rows(rw))[0]
    assert report["complete"] and report["marked_missing"] == 1


def test_unconfirmed_listing_establishes_no_absence(rw, monkeypatch):
    rw.grant("archive", "search")
    service.import_catalog(rw.config, root="archive")
    original = recoll_provider.Provider._call

    def no_signal(self, request, timeout):
        data = original(self, request, timeout)
        data.pop("exhausted", None)  # e.g. an older bridge that does not report exhaustion
        return data

    monkeypatch.setattr(recoll_provider.Provider, "_call", no_signal)
    rw.index = [row for row in rw.index if not row.get("url", "").endswith("notes/injection.md")]
    rw.save()
    report = service.import_catalog(rw.config, root="archive")[0]
    assert report["complete"] is False and report["marked_missing"] == 0
    assert "did not confirm" in report["reason"]
    assert service.search(rw.config, "zebrafinch")["more_may_exist"] is True


def _list_all(prov, root, size: int) -> tuple[list[str], int]:
    ids, listing, position, calls = [], None, {}, 0
    while listing is None or not listing.exhausted:
        listing = prov.enumerate(root, size, 60, **position)
        calls += 1
        ids += [hit.native_id for hit in listing.hits]
        position = {"cursor": listing.cursor} if listing.cursor else {"offset": position.get("offset", 0)
                                                                                + listing.raw_rows}
    return ids, calls


def test_enumeration_pages_by_record_id_and_lists_what_query_paging_lists(rw):
    prov = service.provider(rw.config, "recoll")
    root = prov.roots["archive"]
    by_id, calls = _list_all(prov, root, 2)
    assert prov.enumerate(root, 2, 60).cursor is not None and calls > 2
    assert len(by_id) == len(set(by_id)) and by_id  # every record once, across pages
    (rw.conf / "fake-no-xapian").write_text("")  # no Xapian binding or index: query paging, no cursor
    assert prov.enumerate(root, 2, 60).cursor is None
    by_query, _ = _list_all(prov, root, 2)
    assert sorted(by_query) == sorted(by_id)


def test_a_page_read_while_recoll_commits_is_read_again(rw):
    prov = service.provider(rw.config, "recoll")
    whole = prov.enumerate(prov.roots["archive"], 100, 60)
    (rw.conf / "fake-modified-once").write_text("")
    again = prov.enumerate(prov.roots["archive"], 100, 60)
    assert not (rw.conf / "fake-modified-once").exists()
    assert [h.native_id for h in again.hits] == [h.native_id for h in whole.hits] and again.exhausted


def test_import_follows_the_cursor(rw, monkeypatch):
    rw.grant("archive", "search")
    seen, original = [], recoll_provider.Provider.enumerate

    def spy(self, root, max_rows, timeout, **position):
        seen.append(position)
        return original(self, root, max_rows, timeout, **position)

    monkeypatch.setattr(recoll_provider.Provider, "enumerate", spy)
    report = service.import_catalog(rw.config, root="archive", page_size=2)[0]
    assert report["complete"] and report["occurrences_seen"] == 7
    assert seen[0] == {} and all(set(p) == {"cursor"} for p in seen[1:]) and len(seen) > 2


def test_search_capped_by_filtered_rows_still_says_more(rw):
    rw.grant("archive", "search")
    for n in range(3):
        folder = rw.root / f"roots/archive/dir{n}"
        rw.index.insert(0, {"url": f"file://{folder}", "ipath": "", "mtype": "inode/directory",
                            "rcludi": f"{folder}|", "text": "zebrafinch"})
    rw.save()
    rw.reload("[files.limits]\nmax_scan = 3")
    capped = service.search(rw.config, "zebrafinch")
    assert capped["results"] == [] and capped["more_may_exist"] is True
    rw.path.write_text(rw.path.read_text().replace("max_scan = 3", "max_scan = 500"))
    rw.config = config_mod.load(rw.path)
    full = service.search(rw.config, "zebrafinch economy")
    assert full["results"] and full["more_may_exist"] is False
