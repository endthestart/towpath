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
    config = SimpleNamespace(store_dir=store)
    assert life_photos.propose(config) == []  # not allowed for the life story yet
    grants.grant(store, "folders", "life-evidence", author="owner")
    assert life_photos.propose(config)[0]["proposed"] == 1
    claim, = life.claims(store)
    assert claim.statement == "Took 18 photos (2009 Example Junction)"  # the private export isn't counted
    assert claim.citations[0].ref == "folders:occ_0" and claim.when.earliest.startswith("2009-05-15")
    assert life_photos.propose(config) == []
