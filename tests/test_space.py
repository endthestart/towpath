"""Space: folder sizes, types, largest files and clutter, measured from catalog rows. Invented paths only."""

from contextlib import closing

from towpath import space
from towpath.stores import open_store

ROWS = [
    ("photos/2003/canal.jpg", 4_000_000),
    ("photos/2003/raw/DSC_0001.NEF", 25_000_000),
    ("photos/2003/Thumbs.db", 20_000),
    ("photos/@eaDir/canal.jpg/SYNOPHOTO_THUMB_M.jpg", 30_000),
    ("code/app/node_modules/left-pad/index.js", 1_000),
    ("code/app/node_modules/left-pad/node_modules/tiny/index.js", 500),
    ("code/app/.git/objects/ab/cdef", 2_000),
    ("old-pc/C/Windows/System32/kernel32.dll", 900_000),
    ("old-pc/C/Windows/explorer.exe", 4_000_000),
    ("old-pc/C/Program Files/Tool/tool.exe", 3_000_000),
    ("house/Windows/new-windows-quote.pdf", 50_000),  # a folder named Windows, but not Windows itself
    ("server-copy/etc/hosts", 200),
    ("server-copy/usr/bin/ls", 140_000),
    ("server-copy/var/log/syslog", 10_000),
    ("notes/.DS_Store", 6_000),
    ("notes/~$draft.docx", 160),
    ("notes/draft.docx", 12_000),
    ("README", None),
]


def test_folder_sizes_roll_up_and_files_directly_in_a_folder_are_kept_apart():
    m = space.measure(ROWS)
    folders = m["folders"]
    assert folders[""] == [len(ROWS), sum(s or 0 for _, s in ROWS)]
    assert folders["photos"] == [4, 4_000_000 + 25_000_000 + 20_000 + 30_000]
    assert folders["photos/2003/raw"] == [1, 25_000_000]
    assert m["unknown_size"] == 1 and m["largest"][0] == ["photos/2003/raw/DSC_0001.NEF", 25_000_000]
    types = {ext: (n, b) for ext, n, b in m["types"]}
    assert types["jpg"] == (2, 4_030_000) and types["nef"] == (1, 25_000_000) and "" in types


def test_clutter_is_found_by_name_counting_only_the_outermost_place():
    kinds = {c["kind"]: c for c in space.measure(ROWS)["clutter"]}
    assert [t[0] for t in kinds["packages"]["top"]] == ["code/app/node_modules"]  # the nested one isn't separate
    assert kinds["packages"]["files"] == 2 and kinds["git"]["top"][0][0] == "code/app/.git"
    assert {t[0] for t in kinds["windows"]["top"]} == {"old-pc/C/Windows", "old-pc/C/Program Files"}
    assert kinds["linux"]["top"][0][0] == "server-copy"
    assert {t[0] for t in kinds["thumbnails"]["top"]} == {"photos/@eaDir", "photos/2003"}  # a folder and Thumbs.db
    assert kinds["metadata"]["files"] == 1 and kinds["temp"]["files"] == 1  # .DS_Store; an Office lock file
    assert "house/Windows" not in str(kinds)


def test_a_kept_folder_leaves_the_clutter_list_but_clutter_inside_it_stays():
    rows = [("staging/temp/draft.txt", 10), ("staging/temp/site/node_modules/x.js", 5),
            ("staging/temp/old/temp/scratch.bin", 7), ("work/temp/a.tmp", 3)]
    before = {c["kind"]: [t[0] for t in c["top"]] for c in space.measure(rows)["clutter"]}
    assert set(before["temp"]) == {"staging/temp", "work/temp"}  # the inner temp folder is inside the outer one
    assert before["packages"] == ["staging/temp/site/node_modules"]  # kinds are found separately
    kept = {c["kind"]: [t[0] for t in c["top"]] for c in space.measure(rows, keep={"staging/temp"})["clutter"]}
    assert set(kept["temp"]) == {"staging/temp/old/temp", "work/temp"}  # only the folder itself is kept
    assert kept["packages"] == ["staging/temp/site/node_modules"]


def test_files_are_counted_by_the_month_they_were_last_modified():
    m = space.measure([("a.txt", 10, "2003-05"), ("b.txt", 5, "2003-05"), ("c.txt", 1, "2019-12"), ("d", 2, None)])
    assert m["months"] == [["2003-05", 2, 15], ["2019-12", 1, 1]] and m["undated"] == [1, 2]


def test_the_month_comes_from_the_file_modified_date_whatever_its_place(tmp_path):
    dates = ('[{"basis":"b","meaning":"document-modified","value":"1999-01-01T00:00:00Z"},'
             '{"basis":"b","meaning":"file-modified","value":"2003-05-31T23:00:00Z"}]')
    with closing(open_store(tmp_path, "files", "connect")) as db:
        month = db.execute(f"SELECT {space.MODIFIED_MONTH} FROM (SELECT ? AS dates)", (dates,)).fetchone()[0]
        assert month == "2003-05"
        assert db.execute(f"SELECT {space.MODIFIED_MONTH} FROM (SELECT '[]' AS dates)").fetchone()[0] is None


def test_kept_folders_and_a_new_version_measure_a_root_again(tmp_path):
    with closing(open_store(tmp_path, "files", "connect")) as db:
        for n, (rel, size) in enumerate(ROWS):
            db.execute("""INSERT INTO occurrences (occurrence_id, provider_id, native_id, root_alias, rel_path, members,
                          size, dates, hashes, extraction, first_seen_run, last_seen_run, updated_at)
                          VALUES (?, 'folders', ?, 'docs', ?, '[]', ?, '[]', '{}', '{}', 'r1', 'r1', 'now')""",
                       (f"o{n}", f"n{n}", rel, size))
        db.execute("INSERT INTO runs (run_id, provider_id, kind, started_at, finished_at) "
                   "VALUES ('r1', 'folders', 'import', 'a', 'b')")
        db.execute("INSERT INTO coverage (run_id, provider_id, root_alias, complete, scanned, by_status) "
                   "VALUES ('r1', 'folders', 'docs', 1, 18, '{}')")
        db.commit()
        summary = space.rebuild(db, "folders", "docs", "r1", keep=["code/app/node_modules"])
        assert summary["kept"] == [["code/app/node_modules", 2, 1_500]] and summary["v"] == space.VERSION
        packages = next(c for c in summary["clutter"] if c["kind"] == "packages")
        assert [t[0] for t in packages["top"]] == ["code/app/node_modules/left-pad/node_modules"]  # inside it
        assert space.stale(db, "folders", ["docs"], {"docs": ["code/app/node_modules"]}) == {}
        assert space.stale(db, "folders", ["docs"], {}) == {"docs": "r1"}  # undone
        assert space.stale(db, "folders", ["docs"], {"docs": ["code", "code/app/node_modules"]}) == {"docs": "r1"}
        db.execute("UPDATE space_summary SET body = json_set(body, '$.v', 1)")
        assert space.stale(db, "folders", ["docs"]) == {"docs": "r1"}  # measured by an earlier version


def test_marks_are_read_per_root_from_the_decisions_store(tmp_path):
    from towpath import decisions

    decisions.mark_folder(tmp_path, "folders", "backup", "temp", True)
    decisions.mark_folder(tmp_path, "folders", "backup", "odd:name/x", True)
    decisions.mark_folder(tmp_path, "folders", "media", "cache", True)
    decisions.mark_folder(tmp_path, "folders", "media", "cache", False)
    with closing(open_store(tmp_path, "decisions", "connect")) as db:
        assert space.marks(db, "folders") == {"backup": ["odd:name/x", "temp"]}
        assert space.marks(db, "other") == {}
        log = db.execute("SELECT kind FROM decision_log ORDER BY seq").fetchall()
    assert [k for (k,) in log] == ["space-keep", "space-keep", "space-keep", "space-unkeep"]


def test_archives_and_mailboxes_are_counted_by_what_is_indexed_inside_them(tmp_path):
    gb = 1024 ** 3
    rows = [("mail/old.mbox", 500, "[]"),
            *[("mail/old.mbox", None, f'[{{"index":{i},"kind":"mail-message","name":null}}]') for i in range(3)],
            ("photos.zip", 2 * gb, "[]"),
            *[("photos.zip", None, f'[{{"index":null,"kind":"archive-member","name":"p{i}.jpg"}}]') for i in range(2)],
            ("report.docx", 900, "[]"), ("report.docx", None, '[{"index":null,"kind":"embedded","name":"i.png"}]'),
            ("backup.tar.gz", 3 * gb, "[]"), ("empty.zip", 22, "[]"), ("notes.txt", 5, "[]")]
    with closing(open_store(tmp_path, "files", "connect")) as db:
        for n, (rel, size, members) in enumerate(rows):
            db.execute("""INSERT INTO occurrences (occurrence_id, provider_id, native_id, root_alias, rel_path, members,
                          size, dates, hashes, extraction, first_seen_run, last_seen_run, updated_at)
                          VALUES (?, 'folders', ?, 'docs', ?, ?, ?, '[]', '{}', '{}', 'r1', 'r1', 'now')""",
                       (f"o{n}", f"n{n}", rel, members, size))
        db.commit()
        boxes = space.rebuild(db, "folders", "docs", "r1")["containers"]
    assert boxes["held"] == [2, 2 * gb + 500, 5]  # a document's embedded images don't make it a container
    assert boxes["top"] == [["photos.zip", 2 * gb, 2, "archive"], ["mail/old.mbox", 500, 3, "mailbox"]]
    assert boxes["unindexed"] == [["under 100 MB", 1, 22], ["100 MB to 1 GB", 0, 0], ["over 1 GB", 1, 3 * gb]]


def test_each_import_is_measured_once(tmp_path):
    with closing(open_store(tmp_path, "files", "connect")) as db:
        for n, (rel, size) in enumerate(ROWS):
            db.execute("""INSERT INTO occurrences (occurrence_id, provider_id, native_id, root_alias, rel_path, members,
                          size, dates, hashes, extraction, first_seen_run, last_seen_run, updated_at)
                          VALUES (?, 'folders', ?, 'docs', ?, '[]', ?, '[]', '{}', '{}', 'r1', 'r1', 'now')""",
                       (f"o{n}", f"n{n}", rel, size))
        db.execute("INSERT INTO runs (run_id, provider_id, kind, started_at, finished_at) "
                   "VALUES ('r1', 'folders', 'import', 'a', 'b')")
        db.execute("INSERT INTO coverage (run_id, provider_id, root_alias, complete, scanned, by_status) "
                   "VALUES ('r1', 'folders', 'docs', 1, 18, '{}')")
        db.commit()
        assert space.stale(db, "folders", ["docs", "never-imported"]) == {"docs": "r1"}
        summary = space.rebuild(db, "folders", "docs", "r1")
        assert summary["files"] == len(ROWS) and space.stale(db, "folders", ["docs"]) == {}
        top = space.folder(db, "folders", "docs", "")
        assert top["subfolders"][0][0] == "photos" and top["here_files"] == 1  # README sits at the top
        assert space.folder(db, "folders", "docs", "nowhere") is None
        here = space.files_here(db, "folders", "docs", "photos/2003")
        assert [(name, size) for _, name, size in here] == [("canal.jpg", 4_000_000), ("Thumbs.db", 20_000)]
        assert [name for _, name, _ in space.files_here(db, "folders", "docs", "")] == ["README"]
        assert space.files_here(db, "folders", "docs", "pho") == []  # a prefix of a name isn't the folder
        assert space.summaries(db, "folders", ["docs"])["docs"]["clutter"]


def test_probable_duplicates_share_a_name_and_size_across_roots(tmp_path):
    mb = 1024 * 1024
    rows = [("docs", "2003/taxes.pdf", 3 * mb), ("backup", "old-pc/Documents/2003/TAXES.pdf", 3 * mb),
            ("backup", "old-pc/copy/taxes.pdf", 3 * mb), ("docs", "2004/taxes.pdf", 4 * mb),  # another size
            ("docs", "video/canal.mov", 900 * mb), ("backup", "video/canal.mov", 900 * mb),
            ("docs", "notes/a.txt", 10), ("backup", "notes/a.txt", 10),  # too small to matter
            ("backup", "nas.hbk/Pool/0/1.bucket", 50 * mb), ("backup", "nas.hbk/Pool/1/1.bucket", 50 * mb)]
    with closing(open_store(tmp_path, "files", "connect")) as db:
        for n, (root, rel, size) in enumerate(rows):
            db.execute("""INSERT INTO occurrences (occurrence_id, provider_id, native_id, root_alias, rel_path, members,
                          size, dates, hashes, extraction, first_seen_run, last_seen_run, updated_at)
                          VALUES (?, 'folders', ?, ?, ?, '[]', ?, '[]', '{}', '{}', 'r1', 'r1', 'now')""",
                       (f"o{n}", f"n{n}", root, rel, size))
        db.commit()
        found = space.find_duplicates(db, "folders")
        assert (found["groups"], found["files"], found["wasted"]) == (2, 5, 900 * mb + 2 * 3 * mb)
        movie, taxes = found["top"]
        assert (movie["name"], movie["copies"]) == ("canal.mov", 2)
        assert taxes["copies"] == 3 and ["backup", "old-pc/Documents/2003/TAXES.pdf"] in taxes["places"]
        assert space.duplicates(db, "folders")["groups"] == 2
