"""File discovery contracts: strict config, import boundaries, records, and stores."""

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from towpath import config as config_mod
from towpath.discovery import records
from towpath.stores import RoleError, open_store, require_writer

REPO = Path(__file__).resolve().parents[1]
DISCOVERY = REPO / "src" / "towpath" / "discovery"

FILES = """
[files]
recover_dir = "derived/recovered"

[[files.roots]]
alias = "archive"
path = "/srv/nonexistent-archive"
exclude = ["private/**"]

[[files.providers]]
id = "fx"
adapter = "fixture"
roots = ["archive"]
catalog = "files/catalog.json"
"""


def write(tmp_path, text: str):
    path = tmp_path / "towpath.toml"
    path.write_text(text)
    return config_mod.load(path)


def test_no_files_table_means_no_discovery(tmp_path):
    cfg = write(tmp_path, "[stores]\ndir = 'state'\n")
    assert cfg.files is None


def test_files_table_parses_without_touching_the_filesystem(tmp_path):
    cfg = write(tmp_path, FILES)
    files = cfg.files
    assert files.enabled and files.roots["archive"].path == Path("/srv/nonexistent-archive")
    assert files.providers["fx"].options["catalog"] == tmp_path / "files" / "catalog.json"
    assert files.recover_dir == tmp_path / "derived" / "recovered"
    assert files.limits["max_excerpt_bytes"] == 2000
    assert not (tmp_path / "state").exists() and not (tmp_path / "derived").exists()


@pytest.mark.parametrize("change, message", [
    (('recover_dir = "derived/recovered"', 'recover_dir = "derived/recovered"\nsurprise = 1'), "unknown key"),
    (('alias = "archive"', 'alias = "Archive!"'), "must match"),
    (('adapter = "fixture"', 'adapter = "shell"'), "unknown adapter"),
    (('catalog = "files/catalog.json"', 'catalog = "files/catalog.json"\nurl = "http://x.example"'),
     "unknown key"),
    (('roots = ["archive"]', 'roots = ["elsewhere"]'), "unknown root"),
    (('roots = ["archive"]', 'roots = []'), "non-empty"),
    (('exclude = ["private/**"]', 'exclude = ["../escape"]'), "without '..'"),
    (('exclude = ["private/**"]', 'exclude = ["/abs"]'), "relative"),
    (('recover_dir = "derived/recovered"', 'recover_dir = "/srv/nonexistent-archive/out"'), "recover_dir"),
    (('recover_dir = "derived/recovered"', 'recover_dir = "derived"\n[files.limits]\nmax_results = 100000'),
     "may not exceed"),
    (('recover_dir = "derived/recovered"', 'recover_dir = "derived"\n[files.limits]\ntimeout_seconds = 0'),
     "positive"),
    (('recover_dir = "derived/recovered"', 'enabled = "yes"'), "true or false"),
])
def test_files_config_is_strict(tmp_path, change, message):
    with pytest.raises(config_mod.ConfigError, match=message):
        write(tmp_path, FILES.replace(*change))


def test_nested_roots_and_duplicates_are_rejected(tmp_path):
    nested = FILES + '\n[[files.roots]]\nalias = "inner"\npath = "/srv/nonexistent-archive/inner"\n'
    with pytest.raises(config_mod.ConfigError, match="may not nest"):
        write(tmp_path, nested)
    twice = FILES + '\n[[files.providers]]\nid = "fx"\nadapter = "fixture"\nroots = ["archive"]\ncatalog = "c"\n'
    with pytest.raises(config_mod.ConfigError, match="defined twice"):
        write(tmp_path, twice)


def test_command_is_an_argv_list_never_a_shell_string(tmp_path):
    sist2 = FILES + '\n[[files.providers]]\nid = "s2"\nadapter = "sist2"\nroots = ["archive"]\ncommand = "sist2 ; rm"\n'
    with pytest.raises(config_mod.ConfigError, match="list of strings"):
        write(tmp_path, sist2)
    cfg = write(tmp_path, sist2.replace('command = "sist2 ; rm"', 'command = ["sist2"]'))
    assert cfg.files.providers["s2"].options["command"] == ("sist2",)


def _imported_after(code: str) -> set[str]:
    script = f"import sys\n{code}\nprint('\\n'.join(sorted(sys.modules)))"
    out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=True,
                         env={"PYTHONPATH": str(REPO / "src")}, cwd=REPO).stdout
    return set(out.split())


def test_mail_code_never_imports_discovery(tmp_path):
    path = tmp_path / "towpath.toml"
    path.write_text("[stores]\ndir = 'state'\n")
    mods = _imported_after(f"from towpath import config, connect, scan, cli\nconfig.load({str(path)!r})")
    assert not any(m.startswith("towpath.discovery") for m in mods)


def test_configured_files_imports_only_validation(tmp_path):
    path = tmp_path / "towpath.toml"
    path.write_text(FILES)
    mods = _imported_after(f"from towpath import config\nconfig.load({str(path)!r})")
    discovery = {m for m in mods if m.startswith("towpath.discovery")}
    assert discovery == {"towpath.discovery", "towpath.discovery.config"}
    assert "recoll" not in mods


ALLOWED = {"towpath.canonical", "towpath.credentials", "towpath.stores", "towpath.config", "towpath.fixtures.domains"}
FORBIDDEN = ("towpath.connect", "towpath.scan", "towpath.adapters", "towpath.quota", "towpath.models",
             "towpath.providers", "towpath.decisions", "towpath.cli")


def _imports(path: Path) -> set[str]:
    names = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
            if node.module == "towpath":
                names.update(f"towpath.{a.name}" for a in node.names)
    return names


def test_discovery_imports_only_shared_core():
    for path in DISCOVERY.rglob("*.py"):
        for name in _imports(path):
            assert not name.startswith(FORBIDDEN), f"{path.name} imports {name}"
            if name.startswith("towpath.") and not name.startswith("towpath.discovery"):
                assert name in ALLOWED, f"{path.name} imports {name}"


def test_mail_code_does_not_import_discovery_statically():
    src = REPO / "src" / "towpath"
    for path in src.rglob("*.py"):
        if DISCOVERY in path.parents or path.parent == src and path.name in {"config.py", "cli.py"}:
            continue
        assert not any(n.startswith("towpath.discovery") for n in _imports(path)), path


def test_records_keep_occurrences_apart_and_refuse_fabrication():
    member = (records.Member("archive-member", "mail/backup.mbox"), records.Member("mail-message", index=1),
              records.Member("attachment", "paper.docx", 0))
    a = records.Locator("fx", "archive", "2003/old-mail.zip", member)
    b = records.Locator("fx", "archive", "copies/old-mail.zip", member)
    assert a.occurrence_id != b.occurrence_id
    assert a.display() == "archive:2003/old-mail.zip > mail/backup.mbox > message 1 > paper.docx"
    with pytest.raises(records.RecordError):
        records.Locator("fx", "archive", "../etc/passwd")
    with pytest.raises(records.RecordError):
        records.Occurrence(a, records.Extraction("indexed"), hashes={"sha256": "not-a-digest"})
    with pytest.raises(records.RecordError):
        records.Extraction("probably-fine")
    with pytest.raises(records.RecordError):
        records.DateFact("sometime", "2003", "guess")
    assert records.Extraction("indexed").truncated is None
    assert records.Extraction("indexed", extracted_bytes=10, limit_bytes=10).truncated is True


def test_files_store_is_written_only_by_connect(tmp_path):
    require_writer("files", "connect")
    with pytest.raises(RoleError):
        require_writer("files", "worker")
    conn = open_store(tmp_path, "files", "worker")
    assert conn.execute("SELECT count(*) FROM occurrences").fetchone()[0] == 0
    assert not (tmp_path / "files.db").exists()
    open_store(tmp_path, "files", "connect").close()
    assert (tmp_path / "files.db").exists()
    decisions = open_store(tmp_path, "decisions", "web")
    assert decisions.execute("SELECT count(*) FROM file_grants").fetchone()[0] == 0
