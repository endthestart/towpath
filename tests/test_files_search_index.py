"""The files catalog's trigram index: same answers as a plain substring search, kept in step with the catalog,
backfilled for older catalogs, and actually used. Invented paths only."""

from contextlib import closing
import json
import random
import sqlite3
import string

import pytest

from towpath.stores import SCHEMAS, open_store
from towpath.unified.sources import _fts_query, _like

TEXT = "lower(o.rel_path || ' ' || o.members)"


def query(word: str) -> tuple[str, list]:
    """The catalog query's shape: the trigram index drives when it can, the exact escaped LIKE decides."""
    match = _fts_query([word], [])
    if match is None:
        return f"SELECT o.rel_path FROM occurrences o WHERE {TEXT} LIKE ? ESCAPE '\\' ORDER BY o.rowid", [_like(word)]
    return ("SELECT o.rel_path FROM occurrences_text t JOIN occurrences o ON o.rowid = t.rowid "
            f"WHERE t.text MATCH ? AND {TEXT} LIKE ? ESCAPE '\\' ORDER BY t.rowid", [match, _like(word)])


def add(db, n: int, rel: str, members: str = "[]"):
    db.execute("""INSERT INTO occurrences (occurrence_id, provider_id, native_id, root_alias, rel_path, members,
                  dates, hashes, extraction, first_seen_run, last_seen_run, updated_at)
                  VALUES (?, 'p', ?, 'r', ?, ?, '[]', '{}', '{}', 'run', 'run', 'now')""",
               (f"o{n}", f"n{n}", rel, members))


def search(db, word: str) -> set[str]:
    sql, args = query(word)
    return {r[0] for r in db.execute(sql, args)}


def plain(db, word: str) -> set[str]:
    return {r[0] for r in db.execute(f"SELECT o.rel_path FROM occurrences o WHERE {TEXT} LIKE ? ESCAPE '\\'",
                                     (_like(word),))}


@pytest.fixture
def db(tmp_path):
    with closing(open_store(tmp_path, "files", "connect")) as conn:
        yield conn


def test_answers_match_a_plain_substring_search(db):
    rnd = random.Random(7)
    names = ["DSC_0001.NEF", "DSC10001.NEF", "100%.txt", "100x.txt", "Lock keeper letter.docx", "ab", "a_b",
             "canal-walk.JPG", "Cánal.txt", "notes.md"]
    for n in range(400):
        rel = "/".join("".join(rnd.choices(string.ascii_lowercase, k=5)) for _ in range(3)) + "/" + rnd.choice(names)
        add(db, n, rel, '["mail/backup.mbox:1:paper.docx"]' if n % 50 == 0 else "[]")
    db.commit()
    for word in ("dsc_0001", "dsc", "100%", "nef", ".nef", "lock keeper", "a_b", "ab", "x", "paper.docx",
                 "canal-walk", "nothing-like-this", 'say "hi"'):
        assert search(db, word.lower()) == plain(db, word.lower()), word


def test_the_index_follows_updates_and_deletes(db):
    add(db, 1, "photos/2003/old-name.jpg")
    db.commit()
    assert search(db, "old-name") == {"photos/2003/old-name.jpg"}
    db.execute("UPDATE occurrences SET members = '[\"inside/renamed-member.txt\"]' WHERE occurrence_id = 'o1'")
    assert search(db, "renamed-member") == {"photos/2003/old-name.jpg"}
    db.execute("UPDATE occurrences SET size = 5 WHERE occurrence_id = 'o1'")  # unrelated columns leave it alone
    assert search(db, "old-name") == {"photos/2003/old-name.jpg"}
    db.execute("DELETE FROM occurrences WHERE occurrence_id = 'o1'")
    assert search(db, "old-name") == set() and db.execute("SELECT count(*) FROM occurrences_text").fetchone()[0] == 0


def test_a_catalog_written_before_the_index_is_backfilled_once(tmp_path):
    path = tmp_path / "files.db"
    old = sqlite3.connect(path)
    schema = SCHEMAS["files"].split("-- Substring search over paths")[0]
    old.executescript(schema)
    old.execute("""INSERT INTO occurrences (occurrence_id, provider_id, native_id, root_alias, rel_path, members,
                   dates, hashes, extraction, first_seen_run, last_seen_run, updated_at)
                   VALUES ('o1', 'p', 'n1', 'r', 'documents/taxes/2003.pdf', '[]', '[]', '{}', '{}', 'run', 'run',
                   'x')""")
    old.commit()
    old.close()
    with closing(open_store(tmp_path, "files", "connect")) as db:
        assert search(db, "taxes") == {"documents/taxes/2003.pdf"}
        assert db.execute("SELECT count(*) FROM occurrences_text").fetchone()[0] == 1
    with closing(open_store(tmp_path, "files", "connect")) as db:  # opening again adds nothing twice
        assert db.execute("SELECT count(*) FROM occurrences_text").fetchone()[0] == 1


def test_searches_use_the_index_and_never_sort_every_match(db):
    sql, args = query("keeper")
    plan = " ".join(r[-1] for r in db.execute(f"EXPLAIN QUERY PLAN {sql}", args))
    assert "VIRTUAL TABLE INDEX" in plan and "TEMP B-TREE" not in plan


def test_extensions_narrow_as_alternatives():
    assert _fts_query(["canal"], ["jpg", "nef"]) == '"canal" AND (".jpg" OR ".nef")'
    assert _fts_query(["ab"], ["c"]) is None  # nothing long enough for the trigram index


def test_counts_by_status_are_kept_per_root_not_counted_on_every_read(tmp_path):
    from towpath.discovery import store as fstore

    with closing(open_store(tmp_path, "files", "connect")) as db:
        add(db, 1, "letters/a.txt")
        add(db, 2, "letters/b.txt")
        db.execute("UPDATE occurrences SET extraction = '{\"status\": \"indexed\"}' WHERE occurrence_id = 'o1'")
        db.commit()
        assert fstore.counts(db, "p", "r") == []  # an import that hasn't finished: not counted on a page
        fstore.refresh_counts(db, "p", "r")
        assert fstore.counts(db, "p", "r") == [[None, 1], ["indexed", 1]]
        db.execute("UPDATE occurrences SET missing_since_run = 'run2' WHERE occurrence_id = 'o2'")
        db.commit()
        assert len(fstore.counts(db, "p", "r")) == 2  # the kept counts until the next import recounts
        fstore.refresh_counts(db, "p", "r")
        assert fstore.counts(db, "p", "r") == [["indexed", 1]]
        db.execute("DELETE FROM catalog_counts")
        db.commit()
    with closing(open_store(tmp_path, "files", "connect")) as db:  # a catalog from before the counts: counted once
        assert json.loads(db.execute("SELECT counts FROM catalog_counts").fetchone()[0]) == [["indexed", 1]]
