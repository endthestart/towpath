"""Read-only local UI acceptance checks over invented mail metadata."""

from contextlib import closing
import hashlib
import json
import re
import sqlite3

from django.test import Client, override_settings
import pytest
from typer.testing import CliRunner

from towpath.cli import app
from towpath.stores import open_store
from towpath.web import index
from towpath.web.application import configure


@pytest.fixture
def mail_index(tmp_path):
    with closing(open_store(tmp_path, "source", "connect")) as db:
        db.execute("INSERT INTO sources VALUES ('demo','mail-provider','fixture','{}')")
        for run in ("old", "latest"):
            db.execute("""INSERT INTO runs(run_id,source_id,kind,started_at,finished_at,complete,termination)
                VALUES (?, 'demo', 'full', '2026-03-01T00:00:00+00:00', '2026-03-02T00:00:00+00:00',1,'complete')""",
                       (run,))
        for n in range(30):
            subject = '<script>alert("example")</script> 100% done' if n == 0 else f"Demo conversation {n}"
            db.execute("""INSERT INTO items(item_id,source_id,native_id,subject,from_addr,internal_date,
                first_seen_run,last_seen_run,absent_since_run) VALUES (?,'demo',?,?,?,?,'old','latest',?)""",
                       (f"demo-{n}", str(n), subject, "Avery <avery@example.com>", str(1772409600000 + n * 1000),
                        "latest" if n == 29 else None))
            for run, labels in (("old", ["SENT"]), ("latest", ["INBOX", "UNREAD"] if n == 0 else ["SENT"])):
                db.execute("INSERT INTO observations VALUES (?,?,?,?)",
                           (f"demo-{n}", run, "2026-03-02T00:00:00+00:00", json.dumps(labels)))
        db.execute("""INSERT INTO parts(item_id,part_id,depth,mime_type,filename,size)
            VALUES ('demo-0','1',1,'application/pdf','college_paper.pdf',4096)""")
        db.execute("INSERT INTO cache VALUES ('demo-body',?, '2026-03-02T00:00:00+00:00')",
                   (b"Synthetic body must never appear in this UI",))
        db.commit()
    return tmp_path


@pytest.fixture
def client(mail_index):
    configure(mail_index)
    with override_settings(TOWPATH_LOGIN_REQUIRED=False, TOWPATH_STORE_DIR=mail_index, ALLOWED_HOSTS=["testserver"]):
        yield Client()


def test_search_current_labels_and_literal_names(mail_index):
    stats = index.overview(mail_index)
    assert (stats["total"], stats["sent"], stats["inbox"], stats["attachments"]) == (29, 28, 1, 1)
    assert stats["latest"]["complete"] == 1
    assert index.emails(mail_index, view="sent")["count"] == 28  # Old SENT label isn't current.
    assert index.emails(mail_index, view="inbox")["rows"][0]["item_id"] == "demo-0"
    assert index.emails(mail_index, view="attachments")["count"] == 1
    for query in ("college_paper", "100%", "avery@example.com"):
        assert index.emails(mail_index, query)["count"] == (29 if query.startswith("avery") else 1)
    assert index.emails(mail_index, "' OR 1=1 --")["count"] == 0
    assert index.emails(mail_index, "no_match_")["count"] == 0


def test_bounded_pages_and_query(mail_index):
    first = index.emails(mail_index)
    assert len(first["rows"]) == 25
    assert first["rows"][0]["item_id"] == "demo-28"
    assert (first["count"], first["pages"]) == (29, 2)
    assert len(index.emails(mail_index, page=999)["rows"]) == 4
    assert index.emails(mail_index, page=-10, view="unknown")["page"] == 1
    assert len(index.emails(mail_index, query="x" * 1000)["query"]) == 200


def test_overview_counts_are_kept_until_the_store_changes(mail_index):
    import sqlite3

    first = index.overview(mail_index)
    assert index.overview(mail_index) is first  # the same answer, not counted again
    db = sqlite3.connect(mail_index / "source.db")
    db.execute("UPDATE items SET absent_since_run = 'r9' WHERE item_id = 'demo-0'")
    db.commit()
    db.close()
    assert index.overview(mail_index)["total"] == first["total"] - 1


def test_overview_excludes_personal_rows(client):
    response = client.get("/")
    assert response.status_code == 200
    body = response.content.decode()
    assert "29" in body and "Last sync completed" in body
    assert "avery@example.com" not in body
    assert "Demo conversation" not in body
    assert "college_paper" not in body


def test_routes_escape_mail_and_never_show_bodies(client):
    response = client.get("/email/", {"q": "100%"})
    assert response.status_code == 200
    assert b"&lt;script&gt;" in response.content
    assert b'<script>alert' not in response.content
    detail = client.get("/email/demo-0/")
    assert detail.status_code == 200
    assert b"college_paper.pdf" in detail.content
    assert b"Synthetic body" not in detail.content
    assert b"&lt;avery@example.com&gt;" in detail.content
    assert client.get("/email/missing/").status_code == 404
    assert client.get("/email/", {"page": "bad"}).status_code == 200
    css = client.get("/assets/app.css")
    assert css.status_code == 200 and css["Content-Type"] == "text/css"


def test_no_write_routes_or_untrusted_host(client):
    for path in ("/", "/email/", "/email/demo-0/", "/assets/app.css", "/assets/live.js"):
        assert client.post(path).status_code == 405
        response = client.get(path)
        assert response["Cache-Control"] == "no-store"
        assert response["Referrer-Policy"] == "same-origin"
        assert response["X-Frame-Options"] == "DENY"
        # Only the app's own script file runs: no inline script, no eval.
        assert "script-src 'self';" in response["Content-Security-Policy"]
        assert "unsafe" not in response["Content-Security-Policy"]
    assert client.get("/", HTTP_HOST="untrusted.example.com").status_code == 400


def test_browsing_does_not_modify_database_or_open_credentials(client, mail_index, monkeypatch):
    import towpath.connect
    import towpath.config

    def forbidden(*args, **kwargs):
        pytest.fail("UI attempted connector or credential configuration access")

    monkeypatch.setattr(towpath.connect, "sync", forbidden)
    monkeypatch.setattr(towpath.connect, "fetch_requests", forbidden)
    monkeypatch.setattr(towpath.config, "load", forbidden)
    path = mail_index / "source.db"
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    for route in ("/", "/email/?q=college", "/email/demo-0/", "/assets/app.css"):
        assert client.get(route).status_code == 200
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert sorted(p.name for p in mail_index.iterdir()) == ["source.db"]
    with closing(index.connection(mail_index)) as db:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            db.execute("DELETE FROM items")


def test_cli_starts_on_an_empty_store_folder_but_not_a_missing_one(tmp_path, monkeypatch):
    launched = []
    monkeypatch.setattr("towpath.web.application.launch", lambda *a, **k: launched.append(a))
    result = CliRunner().invoke(app, ["web", "serve", "--store-dir", str(tmp_path)])
    assert result.exit_code == 0 and launched  # a fresh install: setup first, accounts later
    assert list(tmp_path.iterdir()) == []  # the UI creates no store of its own
    missing = CliRunner().invoke(app, ["web", "serve", "--store-dir", str(tmp_path / "absent")])
    plain = re.sub(r"[\s│╭╮╰╯─]+", " ", re.sub(r"\x1b\[[0-9;]*m", "", missing.output))  # however the terminal wraps it
    assert missing.exit_code == 2 and "is not a folder" in plain and not (tmp_path / "absent").exists()
