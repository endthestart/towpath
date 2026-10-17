"""The Attachments page: files in an account's mail from the indexed message structure, each message once."""

import sys
from datetime import datetime, timedelta, timezone
from email import policy
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path

import pytest
from django.test import Client, override_settings

from towpath import connect
from towpath.mail import attachments
from towpath.web import auth
from towpath.web.application import configure

sys.path.insert(0, str(Path(__file__).parent))
import imap_stub as stub  # noqa: E402
from test_mail_threads import World  # noqa: E402

OWNER = "correct horse battery staple"


def with_files(n: int, subject: str, files: list[tuple], days_ago: int = 1, inline: tuple | None = None):
    m = EmailMessage()
    m["From"] = "Landlord <landlord@example.org>"
    m["To"] = "reader@example.com"
    m["Subject"] = subject
    m["Date"] = format_datetime(datetime.now(timezone.utc) - timedelta(days=days_ago))
    m["Message-ID"] = f"<att{n}@example.org>"
    m.set_content("See attached.")
    if inline:
        m.add_related(inline[1], maintype="image", subtype="png", filename=inline[0], disposition="inline")
    for name, data, mtype in files:
        maintype, subtype = mtype.split("/", 1)
        m.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
    return stub.Message(m.as_bytes(policy=policy.SMTP), flags={"\\Seen"})


@pytest.fixture
def world(tmp_path, openai_stub):
    w = World(tmp_path, openai_stub)
    lease = with_files(1, "Lease", [("lease-2026.pdf", b"%PDF synthetic", "application/pdf"),
                                    ("budget.xlsx", b"synthetic", "application/octet-stream"),
                                    ("signature.asc", b"synthetic", "application/pgp-signature")],
                       inline=("logo.png", b"\x89PNG small"))
    w.state.mailboxes["INBOX"].messages[20] = lease
    w.state.mailboxes["Sent"].messages[20] = lease  # the same message in two folders counts once
    connect.sync(w.worker(), w.sid)
    yield w
    w.server.__exit__(None, None, None)


def test_attachments_are_listed_once_with_their_kind_and_signatures_and_logos_are_left_out(world):
    rows = attachments.listing(world.store, world.sid, {})
    assert {r["filename"]: r["kind"] for r in rows} == {"lease-2026.pdf": "documents", "budget.xlsx": "spreadsheets"}
    assert [r["filename"] for r in attachments.listing(world.store, world.sid, {"kind": "signatures"})] == \
        ["signature.asc"]
    assert [r["filename"] for r in attachments.listing(world.store, world.sid, {"q": "budget"})] == ["budget.xlsx"]
    assert attachments.listing(world.store, world.sid, {"sender": "nobody@"}) == []
    found = attachments.summary(world.store, world.sid)
    assert found["total"][0] == 3 and {k[0] for k in found["kinds"]} == {"documents", "spreadsheets", "signatures"}


def test_the_attachments_page(world, monkeypatch):
    monkeypatch.setattr(auth, "_setup_codes", {})
    configure(world.store)
    auth.create_account(world.store, "owner", OWNER)
    with override_settings(TOWPATH_STORE_DIR=world.store, TOWPATH_CONFIG=world.config, ALLOWED_HOSTS=["testserver"]):
        client = Client()
        client.post("/login", {"username": "owner", "password": OWNER})
        page = client.get(f"/mail/{world.sid}/attachments").content.decode()
        assert "lease-2026.pdf" in page and "Spreadsheets" in page and "logo.png" not in page
        page = client.get(f"/mail/{world.sid}/attachments?kind=documents&order=largest").content.decode()
        assert "lease-2026.pdf" in page and "budget.xlsx" not in page
        from towpath.mail import analysis

        analysis.analyze_pending(world.worker())
        item = attachments.listing(world.store, world.sid, {"q": "budget"})[0]["item_id"]
        page = client.get(f"/mail/m/{item}/").content.decode()
        assert "Attachments" in page and "budget.xlsx" in page and "signature.asc" in page


def test_documents_are_planned_as_a_dry_run_then_filed_and_checked(world):
    from towpath.mail import analysis, filing

    analysis.analyze_pending(world.worker())
    assert filing.mode(world.store, world.sid) == "dry-run"
    assert filing.file_pending(world.worker())[0]["planned"] == 1  # the PDF only, once for both folders
    base = filing.root(world.store)
    assert not base.exists()  # a dry run writes nothing
    planned = filing.summary(world.store, world.sid)["latest"][0]["path"]
    assert planned.startswith(f"{world.sid}/") and planned.endswith("lease-2026.pdf")
    assert "landlord@example.org" in planned
    filing.set_mode(world.store, world.sid, "on")
    assert filing.file_pending(world.worker())[0]["filed"] == 1
    copy = base / planned
    assert copy.read_bytes() == b"%PDF synthetic" and world.state.writes == []  # mail itself never changes
    assert filing.file_pending(world.worker()) == []  # once
    world.state.mailboxes["INBOX"].messages[21] = with_files(21, "Same lease again",
                                                             [("lease copy.pdf", b"%PDF synthetic", "application/pdf")])
    connect.sync(world.worker(), world.sid)
    assert filing.file_pending(world.worker())[0]["same"] == 1  # identical content isn't copied again
    assert filing.planned_path("s", {"filename": "../../etc/passwd", "from_addr": "a/b@x", "date_utc": None}) == \
        "s/undated/a_b@x/undated passwd"


def test_the_filing_controls(world, monkeypatch):
    from towpath.mail import filing

    monkeypatch.setattr(auth, "_setup_codes", {})
    configure(world.store)
    auth.create_account(world.store, "owner", OWNER)
    with override_settings(TOWPATH_STORE_DIR=world.store, TOWPATH_CONFIG=world.config, ALLOWED_HOSTS=["testserver"]):
        client = Client()
        client.post("/login", {"username": "owner", "password": OWNER})
        assert "Filing documents" in client.get(f"/mail/{world.sid}/attachments").content.decode()
        client.post(f"/mail/{world.sid}/attachments/filing", {"mode": "off"})
        assert filing.mode(world.store, world.sid) == "off"
        assert client.post(f"/mail/{world.sid}/attachments/filing", {"mode": "loud"}).status_code == 400
