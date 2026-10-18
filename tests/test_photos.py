"""Photo metadata search: headers read in resumable batches (never the private hidden export), typed filters, facets,
and plain-English requests turned into filters by a stand-in model."""

import json
import sys
import textwrap
from types import SimpleNamespace

import pytest

from towpath import photos
from towpath.stores import open_store

EXIF = {
    "trip/IMG_1.jpg": {"Make": "FUJIFILM", "Model": "X100V", "LensModel": "23mm F2",
                       "DateTimeOriginal": "2019:05:14 10:15:00", "GPSLatitude": 45.43, "GPSLongitude": 12.33,
                       "ImageWidth": 6000, "ImageHeight": 4000},
    "trip/IMG_2.JPG": {"Make": "FUJIFILM", "Model": "X100V", "DateTimeOriginal": "2019:05:15 18:00:00"},
    "home/DSC_9.NEF": {"Make": "NIKON CORPORATION", "Model": "NIKON Z 6", "LensModel": "NIKKOR Z 24-70mm f/4 S",
                       "CreateDate": "2021:12:24 09:00:00"},
    "Exports/Hidden/secret.jpg": {"Make": "Apple", "Model": "iPhone"},
}


@pytest.fixture
def library(tmp_path):
    root = tmp_path / "library"
    for rel in [*EXIF, "notes/readme.txt"]:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(b"synthetic")
    fake = tmp_path / "exiftool"
    log = tmp_path / "exiftool.log"
    fake.write_text(textwrap.dedent(f"""\
        #!{sys.executable}
        import json, sys
        args = sys.argv[sys.argv.index("-@") + 1]
        paths = open(args).read().splitlines()
        open({str(log)!r}, "a").write("\\n".join(paths) + "\\n")
        data = {json.dumps({str(root / k): v for k, v in EXIF.items()})!r}
        known = json.loads(data)
        print(json.dumps([dict(SourceFile=p, **known[p]) for p in paths if p in known]))
        """))
    fake.chmod(0o755)
    store = tmp_path / "state"
    with open_store(store, "files", "connect") as db:
        for i, rel in enumerate([*EXIF, "notes/readme.txt"]):
            media = "text/plain" if rel.endswith(".txt") else ("image/x-nikon-nef" if rel.endswith("NEF") else
                                                               "image/jpeg")
            db.execute("INSERT INTO occurrences (occurrence_id, provider_id, native_id, root_alias, rel_path, members, "
                       "media_type, size, dates, hashes, extraction, first_seen_run, last_seen_run, updated_at) "
                       "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (f"occ_{i}", "folders", rel, "pics", rel, "[]", media, 9, "[]", "{}", "{}", "r", "r", "t"))
        db.commit()
    config = SimpleNamespace(store_dir=store, files=SimpleNamespace(roots={"pics": SimpleNamespace(path=root)}),
                             tasks={}, endpoints={})
    return config, str(fake), log


def test_headers_are_read_once_and_the_hidden_export_never(library, monkeypatch):
    config, fake, log = library
    monkeypatch.setattr(photos, "BATCH", 2)
    assert photos.extract_pending(config, exiftool=fake)[0]["read"] == 3
    assert "Hidden" not in log.read_text() and "readme" not in log.read_text()
    assert photos.extract_pending(config, exiftool=fake) == []  # the cursor has passed them all
    p = photos.progress(config.store_dir)
    assert p == {"read": 3, "dated": 3, "located": 1, "share": 100}


def test_filters_and_facets(library):
    config, fake, _ = library
    photos.extract_pending(config, exiftool=fake)
    rows, total = photos.search(config.store_dir, {"camera": "x100v"})
    assert total == 2 and rows[0]["taken"] == "2019-05-15T18:00:00" and rows[0]["provider_id"] == "folders"
    assert photos.search(config.store_dir, {"after": "2021-01-01", "before": "2021-12-31"})[1] == 1
    assert photos.search(config.store_dir, {"located": True})[0][0]["lat"] == 45.43
    assert photos.search(config.store_dir, {"lens": "nikkor z"})[1] == 1
    assert photos.search(config.store_dir, {"text": "trip"})[1] == 2
    f = photos.facets(config.store_dir)
    assert f["cameras"][0] == ("FUJIFILM X100V", 2) and dict(f["years"]) == {"2019": 2, "2021": 1}
    assert sum(n for _, n in f["days"]) == 3 and all(len(d) == 10 for d, _ in f["days"])


def test_a_plain_english_request_becomes_filters(library, tmp_path, openai_stub, monkeypatch):
    from towpath import config as config_mod, connections, decisions

    config, fake, _ = library
    photos.extract_pending(config, exiftool=fake)
    stub = openai_stub(model="local", respond=lambda p: json.dumps(
        {"camera": "X100V", "lens": None, "after": "2019-01-01", "before": "2019-12-31", "located": False,
         "text": None}) if "Turn the owner's request" in p else '{"ok": true}')
    (tmp_path / "towpath.toml").write_text('[stores]\ndir = "state"\n')
    base = config_mod.load(tmp_path / "towpath.toml")
    form = {"base_url": f"http://127.0.0.1:{stub.port}/v1", "model": "local"}
    mid = connections.add_model(base.store_dir, tmp_path / "credentials", form, "t", connections.test_model(form, "t"))
    worker = connections.merged(base, tmp_path / "credentials")
    decisions.grant_model(worker, mid, "content", author="owner")
    with open_store(base.store_dir, "queue", "web") as q:
        q.execute("INSERT INTO photo_questions (text, author, created_at) VALUES ('my Fuji in 2019', 'owner', 't')")
        q.commit()
    assert photos.translate_pending(worker)[0]["translated"] == 1
    with open_store(base.store_dir, "derived", "web") as d:
        filters = json.loads(d.execute("SELECT filters FROM photo_answers").fetchone()[0])
    assert filters["camera"] == "X100V" and filters["after"] == "2019-01-01"
    prompt = next(r["body"]["messages"][-1]["content"] for r in stub.chat_requests()
                  if "Turn the owner's request" in r["body"]["messages"][-1]["content"])
    assert "FUJIFILM X100V" in prompt and "my Fuji in 2019" in prompt


def test_without_exiftool_nothing_happens(library):
    config, _, _ = library
    assert photos.extract_pending(config, exiftool="/nonexistent/exiftool") == []
    assert photos.progress(config.store_dir)["read"] == 0


def test_a_plain_english_search_becomes_a_checked_query(tmp_path, openai_stub):
    from towpath import config as config_mod, connections, decisions, search_translate

    answers = iter(['{"query": "extension:pdf in:\\"files/house\\" after:2019-01-01 before:2019-12-31"}',
                    '{"query": "size:lots"}'])
    stub = openai_stub(model="local", respond=lambda p: next(answers) if "Question:" in p else '{"ok": true}')
    (tmp_path / "towpath.toml").write_text('[stores]\ndir = "state"\n')
    base = config_mod.load(tmp_path / "towpath.toml")
    form = {"base_url": f"http://127.0.0.1:{stub.port}/v1", "model": "local"}
    mid = connections.add_model(base.store_dir, tmp_path / "credentials", form, "t", connections.test_model(form, "t"))
    worker = connections.merged(base, tmp_path / "credentials")
    first = search_translate.ask(base.store_dir, "PDFs about the house from 2019")
    search_translate.translate_pending(worker)  # no permission yet: the question's words
    assert search_translate.answer(base.store_dir, first) == {"query": "PDFs about the house from 2019",
                                                               "status": "words"}
    decisions.grant_model(worker, mid, "content", author="owner")
    second = search_translate.ask(base.store_dir, "PDFs about the house from 2019")
    third = search_translate.ask(base.store_dir, "huge things")
    search_translate.translate_pending(worker)
    assert search_translate.answer(base.store_dir, second)["status"] == "translated"
    assert search_translate.answer(base.store_dir, second)["query"].startswith("extension:pdf")
    assert search_translate.answer(base.store_dir, third) == {"query": "huge things", "status": "words-after-invalid"}


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


def test_dated_documents_become_life_events_from_their_names_only(tmp_path):
    from towpath import life, life_documents, photos
    from towpath.unified import grants

    private = photos.PRIVATE_GLOBS[0][2:]
    names = {"Housing/Lease_2014-08-01.pdf": "Signed a lease", "School/2009/Diploma.pdf": "Graduated",
             "Work/offer-letter 2015-09.docx": "Took a new job", "Car/Bill of Sale 2018-03-02.pdf": "Bought a car",
             "Papers/Passport renewal 2016.pdf": None, "Papers/Software license agreement 2016.pdf": None,
             "Housing/Lease template.pdf": None, "Housing/lease-2014-08-01.jpg": None,
             f"{private}/Lease 2014-08-01.pdf": None, "Future/Lease 2999-01-01.pdf": None}
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
    assert life_documents.propose(config)[0]["proposed"] == 4
    got = {c.statement: c for c in life.claims(store)}
    assert set(got) == {v for v in names.values() if v}
    assert (got["Signed a lease"].when.earliest, got["Signed a lease"].when.precision) == ("2014-08-01", "day")
    assert (got["Graduated"].when.precision, got["Took a new job"].when.precision) == ("year", "month")
    assert got["Signed a lease"].citations[0].ref == "folders:occ_0"
    assert life_documents.propose(config) == []  # nothing changed: not looked at again
