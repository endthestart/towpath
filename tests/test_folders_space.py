"""The Folders connection: measuring space, keeping a folder, and mail-only instances."""

import re
import sys

from django.test import Client, override_settings

from towpath import connections, folders, request_worker
from towpath.web.application import configure


def test_space_is_measured_after_indexing_and_shown_for_searchable_folders(inst, monkeypatch):
    from towpath import space

    monkeypatch.setattr(space, "DUPLICATE_MIN_BYTES", 1)
    lib = inst.library
    (lib / "documents/letters/canal.jpg").write_bytes(b"jpeg")  # the same name and size as a photo
    folders.choose(inst.config, [str(lib / "documents"), str(lib / "photos")])
    inst.recoll_holds("documents/letters/lock-keeper.txt", "documents/taxes/2003.pdf", "photos/2003/canal.jpg",
                      "photos/2003/raw/DSC_0001.NEF", "documents/letters/canal.jpg")
    configure(inst.config.store_dir)
    settings = dict(TOWPATH_STORE_DIR=inst.config.store_dir, TOWPATH_CONFIG=inst.config, ALLOWED_HOSTS=["testserver"],
                    TOWPATH_LOGIN_REQUIRED=False)
    with override_settings(**settings):
        assert "Space is measured when folder indexing finishes" in Client().get("/space/").content.decode()
    connections.start_indexing(inst.config.store_dir, inst.layout.credentials, folders.SID)
    inst.poll_until_idle()
    with override_settings(**settings):
        client = Client()
        page = client.get("/space/").content.decode()
        assert "Your folders" in page and "photos" in page and "documents" in page and ".nef" in page
        assert "DSC_0001.NEF" in page  # among the largest files
        assert "Possible duplicates" in page and "photos/2003/canal.jpg" in page
        assert "documents/letters/canal.jpg" in page
        folder = client.get("/space/folder", {"root": "photos", "path": "2003"}).content.decode()
        assert ">raw<" in folder and "Search for files here" in folder
        assert "Largest files in this folder" in folder and ">canal.jpg<" in folder
        assert "/ref/?r=files-folders" in folder
        assert client.get("/space/folder", {"root": "photos", "path": "elsewhere"}).status_code == 404
        assert client.get("/space/folder", {"root": "private", "path": ""}).status_code == 404
        found = client.get("/search/", {"q": "canal", "source": "files-folders"}).content.decode()
        ref = re.search(r'href="(/ref/\?r=[^"]+)"', found).group(1).replace("&amp;", "&")
        detail = client.get(ref).content.decode()
        assert ">FILE<" in detail and "photos/2003" in detail and "Technical details" in detail
        assert "/space/folder?root=photos&amp;path=2003" in detail and "Search this folder" in detail
    assert request_worker.run_once(inst.config, inst.layout.credentials)["indexing"] == []  # measured once


def test_keeping_a_folder_measures_its_root_again_and_undo_brings_it_back(inst):
    lib = inst.library
    for rel in ("documents/temp/draft.txt", "documents/temp/site/node_modules/x.js"):
        (lib / rel).parent.mkdir(parents=True, exist_ok=True)
        (lib / rel).write_text("x")
    folders.choose(inst.config, [str(lib / "documents")])
    inst.recoll_holds("documents/letters/lock-keeper.txt", "documents/temp/draft.txt",
                      "documents/temp/site/node_modules/x.js")
    configure(inst.config.store_dir)
    connections.start_indexing(inst.config.store_dir, inst.layout.credentials, folders.SID)
    inst.poll_until_idle()
    settings = dict(TOWPATH_STORE_DIR=inst.config.store_dir, TOWPATH_CONFIG=inst.config, ALLOWED_HOSTS=["testserver"],
                    TOWPATH_LOGIN_REQUIRED=False)

    def temp_places(page):
        section = page.split("Temporary files", 1)[1].split("</details>", 1)[0] if "Temporary files" in page else ""
        return re.findall(r">documents/([^<]+)</a>", section)

    with override_settings(**settings):
        client = Client(enforce_csrf_checks=True)
        page = client.get("/space/").content.decode()
        assert temp_places(page) == ["temp"] and "hx-get" not in page  # nothing to poll
        token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', page).group(1)
        form = {"csrfmiddlewaretoken": token, "root": "documents", "path": "temp", "keep": "1", "next": "/space/"}
        assert client.post("/space/keep", {**form, "csrfmiddlewaretoken": "wrong"}).status_code == 403
        assert client.post("/space/keep", {**form, "root": "nowhere"}).status_code == 400
        assert client.post("/space/keep", {**form, "path": "not/measured"}).status_code == 400
        response = client.post("/space/keep", form)
        assert response.status_code == 302 and response["Location"] == "/space/?done=kept&root=documents&path=temp"
        page = client.get(response["Location"]).content.decode()
        assert "Kept <strong>documents/temp</strong>" in page  # the confirmation, with Undo
        assert temp_places(page) == ["temp"] and ">Kept</span>" in page  # the row shows it at once
        assert 'hx-get="/space/status"' in page and "/assets/htmx.min.js" in page and "Measuring documents" in page
        polled = client.get("/space/status", HTTP_HX_REQUEST="true")
        assert "Measuring documents" in polled.content.decode() and "HX-Refresh" not in polled
    inst.poll_until_measured()  # the worker sees the mark and measures that root again
    with override_settings(**settings):
        polled = client.get("/space/status", HTTP_HX_REQUEST="true")
        assert polled["HX-Refresh"] == "true"  # done: the page reloads itself
        page = client.get("/space/").content.decode()
        assert temp_places(page) == [] and 'id="kept"' in page and "1 folder you keep" in page
        assert "Measuring" not in page and "Kept <strong>" not in page  # no message without a fresh change
        assert "documents/temp/site/node_modules" in page  # clutter inside a kept folder still shows
        folder = client.get("/space/folder", {"root": "documents", "path": "temp"}).content.decode()
        assert "You keep this folder" in folder and ">Undo<" in folder
        undo = client.post("/space/keep", {**form, "keep": "0", "next": "/space/folder?root=documents&path=temp"})
        assert undo["Location"] == "/space/folder?root=documents&path=temp&done=undone"
        assert "counts as clutter again" in client.get(undo["Location"]).content.decode()
    inst.poll_until_measured()
    with override_settings(**settings):
        page = client.get("/space/").content.decode()
        assert temp_places(page) == ["temp"] and "Kept, not clutter" not in page


def test_mail_only_instances_never_load_file_discovery(tmp_path):
    import subprocess
    code = ("import sys; from pathlib import Path; from towpath import config, connections, layout; "
            f"l = layout.prepare(Path({str(tmp_path)!r})); connections.merged(config.for_data(l), l.credentials); "
            "print(any(m.startswith('towpath.discovery') for m in sys.modules))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"
