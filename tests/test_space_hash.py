"""Duplicate candidates (same name and size) confirmed or ruled out by content, never reading the private export."""

import json
from types import SimpleNamespace

from towpath import folders, space_hash
from towpath.stores import open_store


def test_candidates_are_hashed_and_judged(tmp_path):
    lib = tmp_path / "library"
    files = {"a/IMG.jpg": b"x" * 2000, "b/IMG.jpg": b"x" * 2000, "c/IMG.jpg": b"y" * 2000,
             "a/movie.mp4": b"m" * 3000, "Exports/Hidden/movie.mp4": b"m" * 3000}
    for rel, data in files.items():
        (lib / rel).parent.mkdir(parents=True, exist_ok=True)
        (lib / rel).write_bytes(data)
    store = tmp_path / "state"
    top = [{"name": "img.jpg", "size": 2000, "copies": 3, "wasted": 4000,
            "places": [["pics", "a/IMG.jpg"], ["pics", "b/IMG.jpg"], ["pics", "c/IMG.jpg"]]},
           {"name": "movie.mp4", "size": 3000, "copies": 2, "wasted": 3000,
            "places": [["pics", "a/movie.mp4"], ["pics", "Exports/Hidden/movie.mp4"]]}]
    with open_store(store, "files", "connect") as db:
        db.execute("INSERT INTO space_duplicates VALUES (?,?,?)",
                   (folders.SID, json.dumps({"groups": 2, "files": 5, "wasted": 7000, "top": top}), "t"))
        db.commit()
    config = SimpleNamespace(store_dir=store, files=SimpleNamespace(roots={"pics": SimpleNamespace(path=lib)}))
    assert space_hash.confirm_pending(config)[0]["hashed"] == 4  # never the private export
    assert space_hash.confirm_pending(config) == []
    with open_store(store, "files", "web") as db:
        hashes = space_hash.verdicts(db, folders.SID)
    assert ("pics", "Exports/Hidden/movie.mp4") not in hashes
    assert space_hash.judge(top[0]["places"], hashes) == {"state": "some copies identical", "same": 2}
    assert space_hash.judge(top[1]["places"], hashes)["state"] == "not compared (a private folder)"
    assert space_hash.judge([["pics", "a/IMG.jpg"], ["pics", "b/IMG.jpg"]], hashes) == \
        {"state": "same content", "same": 2}


def test_a_huge_file_is_compared_by_samples_and_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(space_hash, "FULL_MAX", 1000)  # anything over 1 KB counts as huge here
    monkeypatch.setattr(space_hash, "SAMPLE", 100)
    lib = tmp_path / "library"
    same = b"start" + b"z" * 2990 + b"end!!"
    files = {"a/disk.img": same, "b/disk.img": same, "c/disk.img": b"start" + b"q" * 2990 + b"end!!"}
    for rel, data in files.items():
        (lib / rel).parent.mkdir(parents=True, exist_ok=True)
        (lib / rel).write_bytes(data)
    store = tmp_path / "state"
    places = [["pics", "a/disk.img"], ["pics", "b/disk.img"], ["pics", "c/disk.img"]]
    top = [{"name": "disk.img", "size": 3000, "copies": 3, "wasted": 6000, "places": places}]
    with open_store(store, "files", "connect") as db:
        db.execute("INSERT INTO space_duplicates VALUES (?,?,?)",
                   (folders.SID, json.dumps({"groups": 1, "files": 3, "wasted": 6000, "top": top}), "t"))
        db.commit()
    config = SimpleNamespace(store_dir=store, files=SimpleNamespace(roots={"pics": SimpleNamespace(path=lib)}))
    assert space_hash.confirm_pending(config)[0]["hashed"] == 3
    with open_store(store, "files", "web") as db:
        hashes = space_hash.verdicts(db, folders.SID)
    assert all(h.startswith("sampled:") for h in hashes.values())
    assert space_hash.judge(places[:2], hashes)["state"] == "same content (checked in samples)"
    assert space_hash.judge(places, hashes)["state"] == "some copies identical (checked in samples)"
