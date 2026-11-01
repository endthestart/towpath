"""Photos and dated documents as evidence for the life story, Ask over file names, and the private export."""

from types import SimpleNamespace

from towpath import photos
from towpath.stores import open_store


def test_days_with_many_photos_become_life_evidence_once_allowed(tmp_path):
    from towpath import life, life_photos
    from towpath.unified import grants

    store = tmp_path / "state"
    with open_store(store, "files", "connect") as db:
        for i in range(20):
            rel = f"2009 Example Junction/IMG_{i}.jpg" if i < 18 else f"Exports/Hidden/{i}.jpg"
            db.execute("INSERT INTO occurrences (occurrence_id, provider_id, native_id, root_alias, rel_path, members, "
                       "media_type, size, dates, hashes, extraction, first_seen_run, last_seen_run, updated_at) "
                       "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (f"occ_{i}", "folders", rel, "pics", rel, "[]", "image/jpeg", 9, "[]", "{}", "{}", "r", "r",
                        "t"))
            db.execute("INSERT INTO photo_meta (occurrence_id, taken, extracted_at) VALUES (?, ?, 't')",
                       (f"occ_{i}", f"2009-05-15T1{i % 10}:00:00"))
        db.commit()
    roots = {"pics": SimpleNamespace(path=tmp_path / "pics")}
    config = SimpleNamespace(store_dir=store, files=SimpleNamespace(roots=roots))
    assert life_photos.propose(config) == []  # not allowed for the life story yet
    grants.grant(store, "folders", "life-evidence", author="owner")
    assert life_photos.propose(config)[0]["proposed"] == 1
    claim, = life.claims(store)
    assert claim.statement == "Took 18 photos (2009 Example Junction)"  # the private export isn't counted
    assert claim.citations[0].ref == "folders:occ_0" and claim.when.earliest.startswith("2009-05-15")
    assert life_photos.propose(config) == []


def test_ask_finds_file_names_in_allowed_folders(tmp_path):
    from towpath import life_ask
    from towpath.unified import grants

    store = tmp_path / "state"
    with open_store(store, "files", "connect") as db:
        for i, rel in enumerate(["letters/wedding vows draft.docx", "Exports/Hidden/wedding secret.jpg",
                                 "taxes/2019.pdf"]):
            db.execute("INSERT INTO occurrences (occurrence_id, provider_id, native_id, root_alias, rel_path, members, "
                       "media_type, size, dates, hashes, extraction, first_seen_run, last_seen_run, updated_at) "
                       "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (f"occ_{i}", "folders", rel, "docs", rel, "[]", "application/octet-stream", 9,
                        '[{"meaning": "file-modified", "value": "2009-05-01T10:00:00+00:00"}]', "{}", "{}", "r", "r",
                        "t"))
        db.commit()
    roots = {"docs": SimpleNamespace(path=tmp_path / "docs")}
    config = SimpleNamespace(store_dir=store, files=SimpleNamespace(roots=roots))
    assert life_ask.evidence(config, "When was the wedding?") == []  # folders not allowed for the life story
    grants.grant(store, "folders", "life-evidence", author="owner")
    found = life_ask.evidence(config, "When was the wedding?")
    assert [s["text"] for s in found] == ["docs/letters/wedding vows draft.docx"]  # never the private export
    assert found[0]["ref"] == "folders:occ_0" and found[0]["date"] == "2009-05-01"


def test_a_camera_clock_never_set_gives_no_date():
    from towpath.photos import _taken

    assert _taken("2019:05:14 10:15:00") == "2019-05-14T10:15:00"
    assert _taken("4501:01:01 00:00:00") is None and _taken("0000:00:00 00:00:00") is None
    assert _taken("1964:06:15 00:00:00") == "1964-06-15T00:00:00"


def test_the_private_export_is_kept_out_of_the_catalog_and_recoll(tmp_path):
    from towpath import folders
    from towpath.discovery import refs

    private = photos.PRIVATE_GLOBS[0][2:]  # the private folder, relative
    inside = SimpleNamespace(path=tmp_path / "lib" / private, exclude=())  # a root chosen at the export itself
    above = SimpleNamespace(path=tmp_path / "lib", exclude=())
    assert refs.excluded(inside, "IMG_1.jpg") and refs.excluded(above, f"{private}/IMG_1.jpg")
    assert not refs.excluded(above, "trips/IMG_1.jpg")
    text = folders.write_conf(tmp_path / "conf", [{"path": str(tmp_path / "lib")}, {"path": str(inside.path)}],
                              tmp_path / "s").read_text()
    topdirs = text.split("topdirs = ")[1].split("\n")[0]
    assert f"skippedPaths = \"*/{private}\"" in text and str(inside.path) not in topdirs


def test_private_rows_an_earlier_version_let_in_are_purged_once(tmp_path):
    private = photos.PRIVATE_GLOBS[0][2:]
    store = tmp_path / "state"
    with open_store(store, "files", "connect") as db:
        for i, rel in enumerate(["trips/a.jpg", f"{private}/b.jpg"]):
            db.execute("INSERT INTO occurrences (occurrence_id, provider_id, native_id, root_alias, rel_path, members, "
                       "media_type, size, dates, hashes, extraction, first_seen_run, last_seen_run, updated_at) "
                       "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (f"occ_{i}", "folders", rel, "pics", rel, "[]", "image/jpeg", 9, "[]", "{}", "{}", "r", "r",
                        "t"))
            db.execute("INSERT INTO photo_meta (occurrence_id, taken, extracted_at) VALUES (?, '2019-01-01', 't')",
                       (f"occ_{i}",))
        db.commit()
    roots = {"pics": SimpleNamespace(path=tmp_path / "pics")}
    config = SimpleNamespace(store_dir=store, files=SimpleNamespace(roots=roots))
    assert photos.purge_private(config) == [{"step": "private-purged", "files": 1}]
    assert photos.purge_private(config) == []  # once
    with open_store(store, "files", "web") as db:
        assert [r[0] for r in db.execute("SELECT rel_path FROM occurrences")] == ["trips/a.jpg"]
        assert db.execute("SELECT count(*) FROM photo_meta").fetchone()[0] == 1


def test_runs_of_photo_days_become_one_trip_memory(tmp_path):
    from towpath import life_groups, life_photos
    from towpath.unified import grants

    store = tmp_path / "state"
    with open_store(store, "files", "connect") as db:
        n = 0
        for day in ("2009-05-14", "2009-05-15", "2009-05-17", "2009-08-01"):
            for i in range(16):
                rel = f"2009 Example Junction/{day}_{i}.jpg"
                db.execute("INSERT INTO occurrences (occurrence_id, provider_id, native_id, root_alias, rel_path, "
                           "members, media_type, size, dates, hashes, extraction, first_seen_run, last_seen_run, "
                           "updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           (f"occ_{n}", "folders", rel, "pics", rel, "[]", "image/jpeg", 9, "[]", "{}", "{}", "r",
                            "r", "t"))
                db.execute("INSERT INTO photo_meta (occurrence_id, taken, extracted_at) VALUES (?, ?, 't')",
                           (f"occ_{n}", f"{day}T10:{i:02d}:00"))
                n += 1
        db.commit()
    roots = {"pics": SimpleNamespace(path=tmp_path / "pics")}
    config = SimpleNamespace(store_dir=store, files=SimpleNamespace(roots=roots), tasks={}, endpoints={})
    grants.grant(store, "folders", "life-evidence", author="owner")
    assert life_photos.propose(config)[0]["proposed"] == 4
    life_groups.propose_groups(config)
    trips = [g for g in life_groups.groups(store) if g["kind"] == "trip"]
    assert len(trips) == 1  # three days close together; the August day stands alone
    assert trips[0]["title"] == "Trip: 2009 Example Junction" and len(trips[0]["members"]) == 3


def test_dated_documents_become_life_events_from_their_names_only(tmp_path, monkeypatch):
    from datetime import timedelta

    from towpath import life, life_documents, photos
    from towpath.unified import grants

    private = photos.PRIVATE_GLOBS[0][2:]
    names = {"Housing/Lease_2014-08-01.pdf": "Signed a lease", "School/2009/Diploma.pdf": "Graduated",
             "Work/offer-letter 2015-09.docx": "Took a new job", "Car/Bill of Sale 2018-03-02.pdf": "Bought a car",
             "Papers/Passport renewal 2016.pdf": None, "Papers/Software license agreement 2016.pdf": None,
             "Housing/Lease template.pdf": None, "Housing/lease-2014-08-01.jpg": None,
             f"{private}/Lease 2014-08-01.pdf": None, "Future/Lease 2999-01-01.pdf": None,
             "Identity/Certification 2015.pdf": None, "Lease 1920 Maple Ave 2013.pdf": "Signed a lease",
             "Please read 2015.pdf": None}
    store = tmp_path / "state"
    with open_store(store, "files", "connect") as db:
        for i, rel in enumerate(names):
            db.execute("INSERT INTO occurrences (occurrence_id, provider_id, native_id, root_alias, rel_path, members, "
                       "media_type, size, dates, hashes, extraction, first_seen_run, last_seen_run, updated_at) "
                       "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (f"occ_{i}", "folders", rel, "docs", rel, "[]", None, 9, "[]", "{}", "{}", "r", "r", "t"))
        db.commit()
    config = SimpleNamespace(store_dir=store, files=SimpleNamespace(roots={"docs": SimpleNamespace(path=tmp_path)}))
    assert life_documents.propose(config) == []  # not allowed for the life story yet
    grants.grant(store, "folders", "life-evidence", author="owner")
    assert life_documents.propose(config)[0]["proposed"] == 5
    got = {c.statement: c for c in life.claims(store)}
    assert set(got) == {v for v in names.values() if v}
    assert sorted(c.when.earliest for c in life.claims(store) if c.statement == "Signed a lease") == [
        "2013-01-01", "2014-08-01"]
    assert (got["Signed a lease"].when.earliest, got["Signed a lease"].when.precision) == ("2014-08-01", "day")
    assert (got["Graduated"].when.precision, got["Took a new job"].when.precision) == ("year", "month")
    assert life_documents.when_of("Lease 2999-01.pdf") is None  # a date to come is not a year either
    with open_store(store, "files", "connect") as db:  # a new document arrives
        db.execute("INSERT INTO occurrences (occurrence_id, provider_id, native_id, root_alias, rel_path, members, "
                   "media_type, size, dates, hashes, extraction, first_seen_run, last_seen_run, updated_at) "
                   "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   ("occ_new", "folders", "x", "docs", "Resignation 2020-02-03.pdf", "[]", None, 9, "[]", "{}", "{}",
                    "r", "r", "t"))
        db.commit()
    assert life_documents.propose(config) == []  # looked at again only after a few hours
    monkeypatch.setattr(life_documents, "RECHECK", timedelta(0))
    assert life_documents.propose(config)[0]["proposed"] == 1
    assert life_documents.propose(config) == []  # nothing changed since: not looked at again
