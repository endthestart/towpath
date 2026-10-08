"""The files catalog's trigram index: same answers as a plain substring search, kept in step with the catalog,
backfilled for older catalogs, and actually used. Invented paths only."""

from contextlib import closing
import random
import sqlite3
import string

import pytest

from towpath.stores import SCHEMAS, open_store
from towpath.unified.sources import _like, _text_match

TEXT = "lower(rel_path || ' ' || members)"


def add(db, n: int, rel: str, members: str = "[]"):
    db.execute("""INSERT INTO occurrences (occurrence_id, provider_id, native_id, root_alias, rel_path, members,
                  dates, hashes, extraction, first_seen_run, last_seen_run, updated_at)
                  VALUES (?, 'p', ?, 'r', ?, ?, '[]', '{}', '{}', 'run', 'run', 'now')""",
               (f"o{n}", f"n{n}", rel, members))


def search(db, word: str) -> set[str]:
    clause, args = _text_match(TEXT, word, _like(word))
    return {r[0] for r in db.execute(f"SELECT rel_path FROM occurrences WHERE {clause}", args)}


def plain(db, word: str) -> set[str]:
    return {r[0] for r in db.execute(f"SELECT rel_path FROM occurrences WHERE {TEXT} LIKE ? ESCAPE '\\'",
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
                 "canal-walk", "nothing-like-this"):
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


def test_searches_use_the_index(db):
    clause, args = _text_match(TEXT, "keeper", _like("keeper"))
    plan = " ".join(r[-1] for r in db.execute(f"EXPLAIN QUERY PLAN SELECT * FROM occurrences WHERE {clause}", args))
    assert "VIRTUAL TABLE INDEX" in plan and "occurrences_text" in plan
