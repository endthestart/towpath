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
        assert space.summaries(db, "folders", ["docs"])["docs"]["clutter"]
