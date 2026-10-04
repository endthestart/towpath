"""Tests of a built candidate image, run from the host with Docker.

Collected only when TOWPATH_IMAGE names an image (see tests/conftest.py). TOWPATH_IMAGE_KIND is
``core`` or ``recoll``; TOWPATH_REVISION, when set, must match the image's recorded revision.

Every container runs the way a deployment should: as the image's unprivileged user, with no
network, a read-only root filesystem, no capabilities, sources mounted read-only, and state,
index, recovery, and scratch space on separate writable host folders (disk, not RAM).
"""

import hashlib
import io
import json
import os
import subprocess
import textwrap
import zipfile
from pathlib import Path

import pytest

from towpath.discovery import corpus

IMAGE = os.environ.get("TOWPATH_IMAGE", "")
KIND = os.environ.get("TOWPATH_IMAGE_KIND", "core")
REVISION = os.environ.get("TOWPATH_REVISION")
PAPER_SHA = hashlib.sha256(corpus.docx(corpus.PAPER_TEXT)).hexdigest()
recoll_only = pytest.mark.skipif(KIND != "recoll", reason="needs the recoll image")

CONFIG = """\
[stores]
dir = "/state"

[files]
recover_dir = "/recovered"

[[files.roots]]
alias = "archive"
path = "/data/roots/archive"
exclude = ["private/*"]

[[files.roots]]
alias = "shared"
path = "/data/roots/shared"

[[files.providers]]
id = "fixture"
adapter = "fixture"
roots = ["archive", "shared"]
catalog = "/data/catalog.json"
"""
RECOLL_PROVIDER = """
[[files.providers]]
id = "recoll"
adapter = "recoll"
roots = ["archive"]
confdir = "/index/recoll"
python = "python3"
"""


def pdf_bytes(text: str) -> bytes:
    """A minimal one-page PDF whose text layer holds ``text`` (ASCII)."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = io.BytesIO(), []
    out.write(b"%PDF-1.4\n")
    for n, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % n + body + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1))
    for offset in offsets:
        out.write(b"%010d 00000 n \n" % offset)
    out.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref))
    return out.getvalue()


def odt_bytes(text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/vnd.oasis.opendocument.text")
        z.writestr("META-INF/manifest.xml",
                   '<?xml version="1.0"?><manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:'
                   'xmlns:manifest:1.0"><manifest:file-entry manifest:full-path="/" manifest:media-type='
                   '"application/vnd.oasis.opendocument.text"/><manifest:file-entry manifest:full-path='
                   '"content.xml" manifest:media-type="text/xml"/><manifest:file-entry manifest:full-path='
                   '"meta.xml" manifest:media-type="text/xml"/></manifest:manifest>')
        z.writestr("meta.xml",  # Recoll's internal ODT handler reads meta.xml before content.xml
                   '<?xml version="1.0"?><office:document-meta xmlns:office="urn:oasis:names:tc:opendocument:'
                   'xmlns:office:1.0" xmlns:dc="http://purl.org/dc/elements/1.1/" office:version="1.2">'
                   '<office:meta><dc:title>Minutes</dc:title></office:meta></office:document-meta>')
        z.writestr("content.xml",
                   '<?xml version="1.0"?><office:document-content xmlns:office="urn:oasis:names:tc:'
                   'opendocument:xmlns:office:1.0" xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
                   f'office:version="1.2"><office:body><office:text><text:p>{text}</text:p></office:text>'
                   '</office:body></office:document-content>')
    return buf.getvalue()


def tree_digest(root: Path) -> str:
    h = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        st = path.lstat()
        h.update(f"{path.relative_to(root)}|{st.st_size}|{st.st_mtime_ns}|{oct(st.st_mode)}".encode())
        if path.is_file() and not path.is_symlink():
            h.update(path.read_bytes())
    return h.hexdigest()


class Box:
    """Host folders mounted into each container, and a way to run the image against them."""

    def __init__(self, base: Path):
        self.base = base
        self.data = base / "data"            # corpus, mounted read-only
        corpus.generate(self.data)
        (self.data / "roots" / "archive" / "office").mkdir()
        (self.data / "roots" / "archive" / "office" / "report.pdf").write_bytes(pdf_bytes("heron migration ledger"))
        (self.data / "roots" / "archive" / "office" / "minutes.odt").write_bytes(odt_bytes("kingfisher committee"))
        self.config = base / "config"        # configuration, read-only
        self.config.mkdir()
        (self.config / "towpath.toml").write_text(CONFIG + (RECOLL_PROVIDER if KIND == "recoll" else ""))
        self.writable = {}
        for name in ("state", "recovered", "index", "scratch"):
            path = base / name
            path.mkdir()
            path.chmod(0o777)  # the image's user (uid 10001) differs from the host user
            self.writable[name] = path
        recoll = self.writable["index"] / "recoll"
        recoll.mkdir()
        recoll.chmod(0o777)
        (recoll / "recoll.conf").write_text("topdirs = /data/roots\n")

    def run(self, *args: str, entrypoint: str | None = None, index_ro: bool = False, check: bool = True,
            extra: tuple[str, ...] = ()) -> subprocess.CompletedProcess:
        cmd = ["docker", "run", "--rm", "--network", "none", "--read-only", "--cap-drop", "ALL",
               "--security-opt", "no-new-privileges",
               "-v", f"{self.data}:/data:ro", "-v", f"{self.config}:/config:ro",
               "-v", f"{self.writable['state']}:/state", "-v", f"{self.writable['recovered']}:/recovered",
               "-v", f"{self.writable['index']}:/index" + (":ro" if index_ro else ""),
               "-v", f"{self.writable['scratch']}:/tmp", *extra]
        if entrypoint:
            cmd += ["--entrypoint", entrypoint]
        result = subprocess.run([*cmd, IMAGE, *args], capture_output=True, text=True, timeout=600)
        if check and result.returncode != 0:
            raise AssertionError(f"{args} failed ({result.returncode}): {result.stderr[-2000:]}")
        return result

    def towpath(self, *args: str, **kw):
        return self.run(*args, "--config", "/config/towpath.toml", **kw)

    def json(self, *args: str, **kw):
        return json.loads(self.towpath(*args, **kw).stdout)

    def grant(self, *features: str, root: str = "archive"):
        for feature in features:
            self.towpath("files", "grant", root, feature)


@pytest.fixture
def box(tmp_path):
    return Box(tmp_path)


def inspect() -> dict:
    return json.loads(subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True, text=True,
                                     check=True).stdout)[0]


def test_image_runs_a_cli_not_a_service():
    config = inspect()["Config"]
    assert config["User"] == "10001:10001"
    assert config["Entrypoint"] == ["towpath"] and config["Cmd"] == ["--help"]
    assert not config.get("ExposedPorts") and not config.get("Healthcheck")
    result = subprocess.run(["docker", "run", "--rm", "--network", "none", "--read-only", IMAGE],
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0 and "files" in result.stdout and "connect" in result.stdout


def test_revision_and_build_info_are_recorded(box):
    labels = inspect()["Config"]["Labels"]
    assert labels["org.opencontainers.image.source"].startswith("https://github.com/")
    info = json.loads(box.run("/usr/share/towpath/build-info.json", entrypoint="cat").stdout)
    assert info["image"] == KIND and info["revision"] == labels["org.opencontainers.image.revision"]
    if REVISION:
        assert info["revision"] == REVISION
    assert "MIT" in box.run("/usr/share/licenses/towpath/LICENSE", entrypoint="cat").stdout


def test_runs_unprivileged_with_no_network_and_read_only_mounts(box):
    script = textwrap.dedent("""
        import errno, os, socket
        print("uid", os.getuid())
        print("ifaces", sorted(os.listdir("/sys/class/net")))
        try:
            socket.create_connection(("192.0.2.1", 443), timeout=3)
            print("network reachable")
        except OSError:
            print("network blocked")
        for path in ("/data/roots/archive/new.txt", "/usr/local/new.txt", "/config/new.txt"):
            try:
                open(path, "w")
                print("wrote", path)
            except OSError as exc:
                print("refused", path, errno.errorcode[exc.errno])
        for path in ("/state/ok", "/recovered/ok", "/index/ok", "/tmp/ok"):
            open(path, "w").write("x")
            print("writable", path)
    """)
    out = box.run("-c", script, entrypoint="python3").stdout
    assert "uid 10001" in out and "ifaces ['lo']" in out and "network blocked" in out
    assert "wrote" not in out and out.count("refused") == 3 and "EROFS" in out
    assert out.count("writable") == 4
    assert (box.writable["scratch"] / "ok").exists()  # scratch is the host disk folder, not RAM


def test_file_discovery_needs_no_gmail_or_models(box):
    script = ("import importlib.util as u; print([m for m in ('googleapiclient', 'google_auth_oauthlib', 'openai') "
              "if u.find_spec(m)])")
    assert box.run("-c", script, entrypoint="python3").stdout.strip() == "[]"
    status = box.json("files", "status")
    assert status["configured"] and "fixture" in status["providers"]
    assert box.towpath("config", "check").returncode == 0
    assert not (box.writable["state"] / "source.db").exists()  # no mail store, no sync


def test_fixture_search_excerpt_and_recovery(box):
    before = tree_digest(box.data)
    box.grant("search", "excerpt", "recover")
    found = box.json("files", "search", "zebrafinch economy", "--provider", "fixture")
    paper = next(r for r in found["results"] if r["location"].endswith("message 1 > paper.docx"))
    assert paper["source"]["state"] == "fresh"
    ex = box.json("files", "excerpt", paper["occurrence_id"], "--max-bytes", "40")
    assert ex["state"] == "current" and len(ex["text"].encode()) <= 40
    rec = box.json("files", "recover", paper["occurrence_id"])
    assert rec["sha256"] == PAPER_SHA and rec["recovered_to"].startswith("/recovered/")
    host_copy = box.writable["recovered"] / Path(rec["recovered_to"]).relative_to("/recovered")
    assert hashlib.sha256(host_copy.read_bytes()).hexdigest() == PAPER_SHA
    assert tree_digest(box.data) == before  # sources untouched
    denied = box.towpath("files", "search", "zebrafinch", "--provider", "fixture", check=False)
    assert denied.returncode == 0  # granted root; ungranted ones are simply absent
    assert "shared:" not in denied.stdout and "private/" not in denied.stdout


def test_license_records_cover_every_package(box):
    script = textwrap.dedent("""
        set -e
        dpkg-query -W -f='${Package}\\t${Version}\\t${source:Package}\\t${source:Version}\\n' | sort > /tmp/now.tsv
        cmp /tmp/now.tsv /usr/share/licenses/bundled/packages.tsv && echo "package list current"
        missing=0
        for p in $(cut -f1 /tmp/now.tsv); do
          [ -s /usr/share/licenses/bundled/$p/copyright ] || missing=$((missing+1))
        done
        echo "missing copyright: $missing"
        ls /usr/share/common-licenses/GPL-2 /usr/share/common-licenses/GPL-3 /usr/share/common-licenses/LGPL-2.1
        head -1 /usr/share/licenses/python/packages.tsv
        grep -c . /usr/share/licenses/python/packages.tsv
        grep -i "^typer" /usr/share/licenses/python/packages.tsv
        echo "notice separates: $(grep -c "Towpath's MIT license does not apply" /usr/share/licenses/NOTICE.md)"
        echo "towpath license: $(head -1 /usr/share/licenses/towpath/LICENSE)"
    """)
    out = box.run("-c", script, entrypoint="sh").stdout
    assert "package list current" in out and "missing copyright: 0" in out
    assert "name\tversion\tlicense" in out and "\ntyper\t" in out
    assert "notice separates: 1" in out and "towpath license: MIT License" in out


@pytest.mark.skipif(KIND != "core", reason="CPython from source exists only in the core image")
def test_core_cpython_links_no_source_obliging_library(box):
    script = textwrap.dedent("""
        import importlib.util, pathlib, subprocess
        print("modules", [m for m in ("readline", "_gdbm", "_dbm") if importlib.util.find_spec(m)])
        links = []
        for so in pathlib.Path("/usr/local/lib").rglob("*.so*"):
            out = subprocess.run(["ldd", str(so)], capture_output=True, text=True).stdout
            links += [line for line in out.splitlines() if any(x in line for x in ("libreadline", "libgdbm", "libdb-"))]
        print("links", links)
        print("cpython license", pathlib.Path("/usr/share/licenses/python/CPython-LICENSE.txt").is_file())
    """)
    out = box.run("-c", script, entrypoint="python3").stdout
    assert "modules []" in out and "links []" in out and "cpython license True" in out


@recoll_only
def test_recoll_binding_helpers_and_license_notices(box):
    out = box.run("-c", "for c in recollindex antiword pdftotext unrtf pffexport; do command -v $c; done; "
                  "python3 -c 'import recoll.recoll, recoll.rclextract, lxml; print(\"imports ok\")'",
                  entrypoint="sh").stdout
    for tool in ("recollindex", "antiword", "pdftotext", "unrtf", "pffexport", "imports ok"):
        assert tool in out
    listing = box.run("-c", "cat /usr/share/licenses/bundled/packages.tsv; ls /usr/share/licenses/bundled; "
                      "cat /usr/share/licenses/NOTICE.md", entrypoint="sh").stdout
    for pkg in ("recollcmd", "python3-recoll", "antiword", "poppler-utils", "unrtf", "pff-tools", "python3-lxml"):
        assert f"\n{pkg}\t" in "\n" + listing
    assert "GPL-2.0-or-later" in listing and "towpath-sources:sha256-" in listing
    for pkg in ("recollcmd", "antiword", "pff-tools"):
        assert box.run(f"/usr/share/licenses/bundled/{pkg}/copyright", entrypoint="cat").stdout


@recoll_only
def test_recoll_index_search_recover_in_container(box):
    before = tree_digest(box.data)
    box.run("-c", "/index/recoll", entrypoint="recollindex")
    assert (box.writable["index"] / "recoll" / "xapiandb").is_dir()
    box.grant("search", "excerpt", "recover")
    probe = box.json("files", "probe", "recoll")[0]
    assert probe["available"] and probe["binding"] is True and probe["version"].startswith("Recoll 1.36")
    found = box.json("files", "search", "zebrafinch economy", "--provider", "recoll")
    locations = [r["location"] for r in found["results"]]
    assert "archive:2003/old-mail.zip > mail/backup.mbox > message 1 > paper.docx" in locations
    paper = found["results"][locations.index("archive:2003/old-mail.zip > mail/backup.mbox > message 1 > paper.docx")]
    assert paper["source"]["state"] == "fresh"
    assert corpus.PAPER_TEXT[:30] in box.json("files", "excerpt", paper["occurrence_id"])["text"]
    assert box.json("files", "recover", paper["occurrence_id"])["sha256"] == PAPER_SHA
    for query, name in (("heron migration", "office/report.pdf"), ("kingfisher committee", "office/minutes.odt")):
        hits = box.json("files", "search", query, "--provider", "recoll")["results"]
        assert any(r["location"].endswith(name) for r in hits), (query, hits)
    assert box.json("files", "search", "Private diary", "--provider", "recoll")["results"] == []
    report = box.json("files", "import", "--provider", "recoll", "--root", "archive")[0]
    assert report["complete"] and report["marked_missing"] == 0
    assert tree_digest(box.data) == before


@recoll_only
def test_external_recoll_index_can_be_mounted_read_only(box):
    box.run("-c", "/index/recoll", entrypoint="recollindex")
    box.grant("search", "excerpt")
    found = box.json("files", "search", "zebrafinch economy", "--provider", "recoll", index_ro=True)
    paper = next(r for r in found["results"] if r["location"].endswith("message 1 > paper.docx"))
    assert box.json("files", "excerpt", paper["occurrence_id"], index_ro=True)["state"] == "current"


@recoll_only
def test_stale_index_is_refused_in_container(box):
    box.run("-c", "/index/recoll", entrypoint="recollindex")
    box.grant("search", "excerpt", "recover")
    found = box.json("files", "search", "admin mode", "--provider", "recoll")
    note = found["results"][0]
    note_path = box.data / "roots" / "archive" / "notes" / "injection.md"
    note_path.write_text(note_path.read_text() + "changed on the host after indexing\n")
    described = box.json("files", "describe", note["occurrence_id"])
    assert (described["state"], described["provider_version"], described["source"]) == ("changed", "same", "changed")
    refused = box.towpath("files", "excerpt", note["occurrence_id"], check=False)
    assert refused.returncode == 2 and "error (stale)" in refused.stderr


@recoll_only
def test_compose_example_runs_the_candidate_image(box):
    """deploy/compose.example.yml as shipped: up runs one status check; indexing and search are manual."""
    repo = Path(__file__).resolve().parents[2]
    compose = repo / "deploy" / "compose.example.yml"
    config_dir = box.base / "compose-config"
    config_dir.mkdir()
    (config_dir / "towpath.toml").write_text((repo / "deploy" / "towpath.toml.example").read_text())
    (box.writable["index"] / "recoll" / "recoll.conf").write_text((repo / "deploy" / "recoll.conf.example").read_text())
    env = dict(os.environ, TOWPATH_IMAGE=IMAGE, TOWPATH_SOURCE_DIR=str(box.data / "roots" / "archive"),
               TOWPATH_CONFIG_DIR=str(config_dir), TOWPATH_INDEX_DIR=str(box.writable["index"]),
               TOWPATH_STATE_DIR=str(box.writable["state"]), TOWPATH_RECOVERED_DIR=str(box.writable["recovered"]),
               TOWPATH_SCRATCH_DIR=str(box.writable["scratch"]))
    base = ["docker", "compose", "-f", str(compose), "-p", f"towpath-test-{os.getpid()}"]

    def compose_run(*args):
        result = subprocess.run([*base, *args], env=env, capture_output=True, text=True, timeout=600)
        assert result.returncode == 0, result.stderr[-2000:]
        return result

    try:
        compose_run("up", "--no-build", "--pull", "never", "--abort-on-container-exit", "--exit-code-from", "towpath")
        assert not (box.writable["index"] / "recoll" / "xapiandb").exists()  # up never indexes
        compose_run("--profile", "index", "run", "--rm", "--no-deps", "recoll-index")
        assert (box.writable["index"] / "recoll" / "xapiandb").is_dir()
        compose_run("run", "--rm", "towpath", "files", "grant", "archive", "search", "--config", "/config/towpath.toml")
        found = json.loads(compose_run("run", "--rm", "towpath", "files", "search", "zebrafinch economy",
                                       "--config", "/config/towpath.toml").stdout)
        assert any(r["location"].endswith("message 1 > paper.docx") for r in found["results"])
    finally:
        subprocess.run([*base, "--profile", "index", "down", "--remove-orphans"], env=env, capture_output=True,
                       timeout=300)
