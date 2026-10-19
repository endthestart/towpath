"""The host's dump script: consistent, checked, compact copies of the stores; nothing secret; a restore check."""

import gzip
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "deploy" / "backup" / "towpath-dump.py"
spec = importlib.util.spec_from_file_location("towpath_dump", SCRIPT)
dump = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dump)


@pytest.fixture
def data(tmp_path):
    root = tmp_path / "towpath"
    (root / "state").mkdir(parents=True)
    (root / "config").mkdir()
    (root / "credentials").mkdir()
    (root / "credentials" / "fastmail.secret").write_text("app-password")
    (root / "config" / "towpath.toml").write_text('[stores]\ndir = "state"\n')
    (root / "state" / "web-login.json").write_text('{"hash": "x"}')
    for name in ("decisions.db", "mail.db", "files.db", "thumbs.db"):
        con = sqlite3.connect(root / "state" / name)
        con.execute("CREATE TABLE t (x)")
        con.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(100)])
        con.commit()
        con.close()
    return root, tmp_path / "dumps"


def test_a_nightly_dump_is_checked_compact_and_holds_no_secrets(data):
    root, dest = data
    out = dump.run(root, dest, weekly=False, stamp="20261019T0815Z")
    manifest = json.loads((out / "manifest.json").read_text())
    assert [s["store"] for s in manifest["stores"]] == ["decisions.db", "mail.db"]  # files.db weekly, thumbs never
    assert all(s["integrity"] == "ok" for s in manifest["stores"])
    names = {p.name for p in out.iterdir()}
    assert "fastmail.secret" not in names and not any("credential" in n for n in names)
    with gzip.open(out / "mail.db.gz") as g:
        assert g.read(16) == b"SQLite format 3\x00"
    assert dump.verify(dest)["restore_check"] == "ok"
    weekly = dump.run(root, dest, weekly=True, stamp="20261020T0815Z")
    assert "files.db" in [s["store"] for s in json.loads((weekly / "manifest.json").read_text())["stores"]]


def test_an_unfinished_write_in_the_copy_is_rolled_back_like_after_a_crash(data, tmp_path):
    root, dest = data
    db = root / "state" / "decisions.db"
    con = sqlite3.connect(db)
    con.execute("BEGIN")
    con.execute("DELETE FROM t")  # in progress: the rollback journal is on disk
    snap = tmp_path / "snap"
    import shutil

    shutil.copytree(root, snap)  # what a snapshot taken now would hold
    con.rollback()
    con.close()
    out = dump.run(snap, dest, weekly=False, stamp="20261019T0815Z")
    restored = tmp_path / "r.db"
    with gzip.open(out / "decisions.db.gz") as g:
        restored.write_bytes(g.read())
    assert sqlite3.connect(restored).execute("SELECT count(*) FROM t").fetchone()[0] == 100


def test_a_damaged_dump_fails_the_restore_check_and_old_dumps_are_pruned(data):
    root, dest = data
    for day in range(1, 21):
        dump.run(root, dest, weekly=day % 7 == 0, stamp=f"202610{day:02d}T0815Z")
    gone = dump.prune(dest, None)
    assert gone == [f"202610{d:02d}T0815Z" for d in range(2, 7)]  # the last 14, and the first of the month, stay
    (dest / "20261020T0815Z" / "mail.db.gz").write_bytes(b"broken")
    with pytest.raises(RuntimeError, match="checksum"):
        dump.verify(dest)
