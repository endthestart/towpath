"""Photo previews: the owner's three rules. No model ever reads a photo or a preview; nothing for the private export;
nothing for a photo hidden (or deleted) in Apple Photos, judged by the library's own database. And off means none."""

import os
import sqlite3
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

from towpath import apple_photos, photos, thumbs
from towpath.stores import open_store

PRIVATE = photos.PRIVATE_GLOBS[0][2:]  # the private folder, never typed out


def photos_sqlite(path: Path, rows, table="ZASSET"):
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    if table:
        con.execute(f"CREATE TABLE {table} (Z_PK INTEGER PRIMARY KEY, ZHIDDEN INTEGER, ZTRASHEDSTATE INTEGER, "
                    "ZDIRECTORY TEXT, ZFILENAME TEXT, ZUUID TEXT)")
        con.executemany(f"INSERT INTO {table} (ZHIDDEN, ZTRASHEDSTATE, ZDIRECTORY, ZFILENAME, ZUUID) VALUES "
                        "(?,?,?,?,?)", rows)
    else:
        con.execute("CREATE TABLE other (x)")
    con.commit()
    con.close()


@pytest.fixture
def world(tmp_path):
    root = tmp_path / "library"
    files = {"trip/IMG_1.jpg": "plain", f"{PRIVATE}/secret.jpg": "private",
             "Lib.photoslibrary/originals/A/AAAA-1.jpg": "visible original",
             "Lib.photoslibrary/originals/B/BBBB-2.jpg": "hidden original",
             "Lib.photoslibrary/originals/C/CCCC-3.jpg": "deleted original",
             "Lib.photoslibrary/resources/derivatives/A/AAAA-1_1_105_c.jpeg": "library's own copy",
             "Old.photoslibrary/originals/D/DDDD-4.jpg": "in a library Towpath can't read"}
    for rel in files:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(b"synthetic")
    photos_sqlite(root / "Lib.photoslibrary/database/Photos.sqlite",
                  [(0, 0, "A", "AAAA-1.jpg", "AAAA-1"), (1, 0, "B", "BBBB-2.jpg", "BBBB-2"),
                   (0, 1, "C", "CCCC-3.jpg", "CCCC-3")])
    photos_sqlite(root / "Old.photoslibrary/database/Photos.sqlite", [], table=None)
    fake = tmp_path / "exiftool"
    log = tmp_path / "exiftool.log"
    fake.write_text(textwrap.dedent(f"""\
        #!{sys.executable}
        import base64, json, sys
        paths = open(sys.argv[sys.argv.index("-@") + 1]).read().splitlines()
        open({str(log)!r}, "a").write("\\n".join(paths) + "\\n")
        jpeg = lambda p: "base64:" + base64.b64encode(b"\\xff\\xd8thumb " + p.encode() + b"\\xff\\xd9").decode()
        print(json.dumps([dict(SourceFile=p, ThumbnailImage=jpeg(p), Orientation=6) for p in paths]))
        """))
    fake.chmod(0o755)
    store = tmp_path / "state"
    with open_store(store, "files", "connect") as db:
        for i, rel in enumerate(files):
            db.execute("INSERT INTO occurrences (occurrence_id, provider_id, native_id, root_alias, rel_path, members, "
                       "media_type, size, dates, hashes, extraction, first_seen_run, last_seen_run, updated_at) "
                       "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (f"occ_{i}", "folders", rel, "pics", rel, "[]", "image/jpeg", 9, "[]", "{}", "{}", "r", "r",
                        "t"))
            db.execute("INSERT INTO photo_meta (occurrence_id, taken, model, extracted_at) VALUES (?,?,?,?)",
                       (f"occ_{i}", "2019-05-14T10:00:00", "X100V", "t"))
        db.commit()
    config = SimpleNamespace(store_dir=store, files=SimpleNamespace(roots={"pics": SimpleNamespace(path=root)}),
                             tasks={}, endpoints={})
    by = {why: f"occ_{i}" for i, why in enumerate(files.values())}
    return config, str(fake), log, by, root


def run(config, fake):
    apple_photos.mark_hidden(config)
    return thumbs.thumbs_pending(config, exiftool=fake)


def test_previews_are_made_on_by_default_and_upright(world):
    config, fake, _, by, _ = world
    assert thumbs.enabled(config.store_dir)
    run(config, fake)
    got = thumbs.preview(config.store_dir, by["plain"])
    assert got["jpeg"].startswith(b"\xff\xd8") and got["orientation"] == 6
    assert thumbs.preview(config.store_dir, by["visible original"]) is not None


def test_rule_nothing_for_the_private_export(world):
    config, fake, log, by, _ = world
    run(config, fake)
    assert thumbs.preview(config.store_dir, by["private"]) is None
    assert PRIVATE not in log.read_text()  # its file is never even handed to ExifTool


def test_rule_nothing_for_photos_hidden_or_deleted_in_apple_photos(world):
    config, fake, log, by, root = world
    run(config, fake)
    for why in ("hidden original", "deleted original", "library's own copy", "in a library Towpath can't read"):
        assert thumbs.preview(config.store_dir, by[why]) is None, why
    read = log.read_text()
    assert "BBBB-2" not in read and "CCCC-3" not in read and "DDDD-4" not in read  # never opened
    rows, total = photos.search(config.store_dir, {"camera": "x100v"})
    assert by["hidden original"] not in {r["occurrence_id"] for r in rows}  # left out of photo lists too
    # The owner hides a photo later: its preview goes at the next check.
    con = sqlite3.connect(root / "Lib.photoslibrary/database/Photos.sqlite")
    con.execute("UPDATE ZASSET SET ZHIDDEN = 1 WHERE ZUUID = 'AAAA-1'")
    con.commit()
    con.close()
    later = os.stat(root / "Lib.photoslibrary/database/Photos.sqlite").st_mtime_ns + 10**9
    os.utime(root / "Lib.photoslibrary/database/Photos.sqlite", ns=(later, later))
    assert run(config, fake)[0]["dropped"] == 1
    assert thumbs.preview(config.store_dir, by["visible original"]) is None


def test_rule_no_model_ever_reads_a_photo_or_preview(world, monkeypatch):
    from towpath.models import gateway

    config, fake, _, by, _ = world
    monkeypatch.setattr(gateway, "infer", lambda *a, **k: pytest.fail("a model was asked about a photo"))
    run(config, fake)
    assert thumbs.preview(config.store_dir, by["plain"]) is not None
    for module in (thumbs, apple_photos):
        assert "towpath.models" not in Path(module.__file__).read_text()  # nothing here can reach a model


def test_off_means_none_made_none_shown_and_old_ones_deleted(world):
    config, fake, _, by, _ = world
    run(config, fake)
    thumbs.set_enabled(config.store_dir, False)
    assert thumbs.preview(config.store_dir, by["plain"]) is None
    assert run(config, fake)[0]["step"] == "photo-previews-deleted"
    with open_store(config.store_dir, "thumbs", "web") as t:
        assert t.execute("SELECT count(*) FROM thumbs").fetchone()[0] == 0


def test_the_photos_page_and_preview_route(world, monkeypatch):
    from django.test import Client, override_settings

    from towpath.web import auth
    from towpath.web.application import configure

    config, fake, _, by, _ = world
    run(config, fake)
    monkeypatch.setattr(auth, "_setup_codes", {})
    configure(config.store_dir)
    auth.create_account(config.store_dir, "owner", "correct horse battery staple")
    with override_settings(TOWPATH_STORE_DIR=config.store_dir, TOWPATH_CONFIG=config, ALLOWED_HOSTS=["testserver"]):
        assert Client().get(f"/photos/preview/{by['plain']}").status_code == 302  # signed out: to the sign-in page
        client = Client()
        client.post("/login", {"username": "owner", "password": "correct horse battery staple"})
        page = client.get("/photos/", {"camera": "x100v"}).content.decode()
        assert f"/photos/preview/{by['plain']}" in page and f"/photos/preview/{by['hidden original']}" not in page
        image = client.get(f"/photos/preview/{by['plain']}")
        assert image.status_code == 200 and image["Content-Type"] == "image/jpeg"
        assert client.get(f"/photos/preview/{by['hidden original']}").status_code == 404
        assert client.get(f"/photos/preview/{by['private']}").status_code == 404
        client.post("/photos/previews", {"on": "0"})
        assert client.get(f"/photos/preview/{by['plain']}").status_code == 404


def test_the_year_page_shows_previews_of_its_busiest_days_but_never_a_hidden_photo(world, monkeypatch):
    from django.test import Client, override_settings

    from towpath.unified import grants
    from towpath.web import auth
    from towpath.web.application import configure

    config, fake, _, by, _ = world
    run(config, fake)
    grants.grant(config.store_dir, "folders", "life-evidence", author="owner")
    monkeypatch.setattr(auth, "_setup_codes", {})
    configure(config.store_dir)
    auth.create_account(config.store_dir, "owner", "correct horse battery staple")
    with override_settings(TOWPATH_STORE_DIR=config.store_dir, TOWPATH_CONFIG=config, ALLOWED_HOSTS=["testserver"]):
        client = Client()
        client.post("/login", {"username": "owner", "password": "correct horse battery staple"})
        page = client.get("/life/year/2019").content.decode()
        assert f"/photos/preview/{by['plain']}" in page and f"/photos/preview/{by['hidden original']}" not in page
        assert f"/photos/preview/{by['private']}" not in page
