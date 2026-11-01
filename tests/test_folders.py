"""Folders on the NAS from the Connections page: picking, Recoll configuration, background indexing with
progress and pause, and the paged import into search. A fake ``recollindex`` and the stub Recoll binding stand
in for Recoll; the folders themselves are invented and only ever read."""

import os

from django.test import Client, override_settings
import pytest

from towpath import connections, folders
from towpath.web.application import configure


def test_the_picker_shows_two_levels_and_marks_unreadable_folders(inst):
    tree = {t["name"]: t for t in folders.tree()}
    assert sorted(tree) == ["documents", "music", "photos", "private"]
    assert [c["name"] for c in tree["documents"]["children"]] == ["letters", "taxes"]
    if os.geteuid() != 0:
        assert tree["private"]["readable"] is False


def test_choosing_folders_validates_drops_nested_ones_and_grants_search(inst):
    lib = inst.library
    with pytest.raises(connections.ConnectionProblem, match="not a folder in the library"):
        folders.choose(inst.config, [str(lib.parent)])
    folders.choose(inst.config, [str(lib / "documents"), str(lib / "documents/letters"), str(lib / "photos")])
    c = connections.get(inst.config.store_dir, folders.SID)
    assert [(r["alias"], r["rel"]) for r in c["settings"]["roots"]] == [
        ("photos", "photos"), ("documents", "documents")]
    merged = connections.merged(inst.config, inst.layout.credentials)
    assert set(merged.files.roots) == {"photos", "documents"}
    from towpath.discovery import policy
    assert policy.granted(merged) == {"photos": {"search"}, "documents": {"search"}}
    folders.choose(inst.config, [str(lib / "documents"), str(lib / "music")])  # aliases are kept across changes
    roots = connections.get(inst.config.store_dir, folders.SID)["settings"]["roots"]
    assert [r["alias"] for r in roots] == ["music", "documents"]


def test_recoll_configuration_lists_everything_and_reads_media_by_name_only(inst, tmp_path):
    conf = folders.write_conf(tmp_path / "conf", [{"path": "/library/My Photos"}, {"path": "/library/docs"}],
                              tmp_path / "scratch")
    text = conf.read_text()
    assert 'topdirs = "/library/My Photos" "/library/docs"' in text
    assert ".nef" in text and ".mp4" in text and "followLinks = 0" in text
    # Recoll's own defaults skip caches, .git and tmp folders; replacing them keeps those findable.
    assert "skippedNames = .zfs .snapshot\n" in text and "skippedNames+" not in text
    # Without this, Recoll opens every unrecognised file to guess its type, then lists it by name anyway.
    assert "usesystemfilecommand = 0" in text and "thrTCounts = 2 1 1" in text
    assert "idxflushmb = 512\n" in text  # large batches: far fewer merges into a big index


def test_indexing_settings_are_validated_saved_and_written_to_recolls_configuration(inst, tmp_path):
    folders.choose(inst.config, [str(inst.library / "documents")])
    assert folders.options(connections.get(inst.config.store_dir, folders.SID)) == folders.DEFAULTS
    form = {**folders.DEFAULTS, "threads": "6", "name_only": ".ISO .vmdk", "sniff": "on", "compressed_limit_mb": "0",
            "batch_mb": "1024"}
    folders.set_options(inst.config, form)
    c = connections.get(inst.config.store_dir, folders.SID)
    assert c["settings"]["indexing"] == {"threads": 6, "name_only": ".iso .vmdk", "sniff": True,
                                         "compressed_limit_mb": 0, "batch_mb": 1024}  # only what differs
    folders.choose(inst.config, [str(inst.library / "documents"), str(inst.library / "photos")])
    o = folders.options(connections.get(inst.config.store_dir, folders.SID))  # choosing again keeps them
    assert (o["threads"], o["sniff"]) == (6, True)
    text = folders.write_conf(tmp_path / "conf", [{"path": "/library/docs"}], tmp_path / "s", o).read_text()
    assert "thrTCounts = 6 3 1" in text and "usesystemfilecommand = 1" in text and "compressedfilemaxkbs = 0\n" in text
    assert "noContentSuffixes+ = .iso .vmdk\n" in text and "idxflushmb = 1024\n" in text
    for bad in ({"threads": "0"}, {"threads": "many"}, {"name_only": "iso"}, {"skip": 'a"b'},
                {"skip": "x\nloglevel = 6"}, {"skip": "a=b"}, {"text_limit_mb": "-1"}):
        with pytest.raises(connections.ConnectionProblem):
            folders.set_options(inst.config, {**folders.DEFAULTS, **bad})
    assert folders.options(connections.get(inst.config.store_dir, folders.SID))["threads"] == 6  # unchanged


def test_the_folders_page_changes_indexing_settings(inst, monkeypatch):
    folders.choose(inst.config, [str(inst.library / "documents")])
    configure(inst.config.store_dir)
    with override_settings(TOWPATH_STORE_DIR=inst.config.store_dir, TOWPATH_CONFIG=inst.config,
                           ALLOWED_HOSTS=["testserver"], ROOT_URLCONF="towpath.web.connection_urls",
                           TOWPATH_LOGIN_REQUIRED=False, TOWPATH_CREDENTIALS_DIR=inst.layout.credentials):
        client = Client()
        page = client.get("/connections/folders/").content.decode()
        assert "Files read at once" in page and 'value="2"' in page and "Skip these names" in page
        form = {**{k: v for k, v in folders.DEFAULTS.items() if v is not False}, "threads": "3"}
        response = client.post("/connections/folders/indexing", form)
        assert response.status_code == 302 and response["Location"] == "/connections/folders/?saved=indexing"
        assert "Saved." in client.get(response["Location"]).content.decode()
        refused = client.post("/connections/folders/indexing", {**form, "threads": "40"})
        assert refused.status_code == 400 and "between 1 and 16" in refused.content.decode()
        assert 'value="40"' in refused.content.decode()  # what was typed stays in the form
        client.post("/connections/folders/indexing", {"reset": "1"})
    assert folders.options(connections.get(inst.config.store_dir, folders.SID)) == folders.DEFAULTS


def test_indexing_again_says_it_checks_for_changes(inst):
    folders.choose(inst.config, [str(inst.library / "documents")])
    configure(inst.config.store_dir)
    status = {"phase": 1, "filesdone": 179_065, "docsdone": 30, "totfiles": 6_954_981}
    connections._update(inst.config.store_dir, folders.SID, indexing="running", progress=folders._progress(
        status, "reading", previous=folders._carried({"progress": {"in_search": 951_326}})))
    with override_settings(TOWPATH_STORE_DIR=inst.config.store_dir, TOWPATH_CONFIG=inst.config,
                           ALLOWED_HOSTS=["testserver"], ROOT_URLCONF="towpath.web.connection_urls",
                           TOWPATH_LOGIN_REQUIRED=False, TOWPATH_CREDENTIALS_DIR=inst.layout.credentials):
        panel = Client().get("/connections/folders/live").content.decode()
    assert "Checking for changes" in panel and "Files checked" in panel and "951,326" in panel
    assert "each file inside an archive" in panel  # search holds members too, so it can pass the files read
    assert "of about 6,954,981" in panel and "30 new or changed" in panel
