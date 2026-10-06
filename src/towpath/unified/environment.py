"""A synthetic environment for unified discovery: Gmail, optional IMAP, a files corpus and an inventory manifest.

Everything is invented and uses reserved domains. Layout under ``out``:

    towpath.toml                   config: Gmail fixture source, optional IMAP source, [files] roots and providers
    mail/account_u.json            Gmail-shaped account (canal society mail, a NEF attachment)
    files/...                      the file discovery corpus (towpath.discovery.corpus)
    files/manifests/photos.jsonl   inventory of a photos root: NEF files, a sidecar, an unreadable scan, a
                                   nested archive member, one rejected entry; its scope is declared incomplete
    files/roots/photos/            the photos root (empty: the manifest describes files not present here)

The IMAP source, when requested, points at a loopback synthetic server (``tests/imap_stub.py``) and
reads its password from ``TOWPATH_FIXTURE_IMAP_PASSWORD``.
"""

import hashlib
import json
import random
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path

from towpath.discovery import corpus
from towpath.fixtures.generator import Account

HOLDER = "member@example.com"
IMAP_PASSWORD_ENV = "TOWPATH_FIXTURE_IMAP_PASSWORD"
NEF_BYTES = b"MM\x00*" + b"synthetic raw sensor data " * 8

PHOTO_MANIFEST = [
    {"format": "towpath.files.manifest/1", "manifest_id": "photos-inventory-1", "root": "photos",
     "produced_by": {"tool": "synthetic inventory", "version": "1"}, "captured_at": "2026-10-01T00:00:00Z",
     "scope": {"complete": False, "notes": "synthetic: the 2012 folder is still being listed"}},
    {"path": "2011/aqueduct/DSC_0042.NEF", "size": 24117248, "mtime": "2011-08-20T18:31:00Z",
     "media_type": "image/x-nikon-nef"},
    {"path": "2011/aqueduct/DSC_0043.nef", "size": 23980032, "mtime": "2011-08-20T18:32:00Z",
     "media_type": "image/x-nikon-nef"},
    {"path": "2011/aqueduct/DSC_0043.xmp", "size": 4211, "mtime": "2011-08-21T09:00:00Z",
     "media_type": "application/rdf+xml"},
    {"path": "2004/scans/ledger.tif", "size": 9000, "mtime": 1073001600, "media_type": "image/tiff",
     "status": "unreadable", "detail": "the inventory tool could not open this file"},
    {"path": "2003/camera-backup.zip", "members": [["archive-member", "DCIM/DSC_0001.NEF", None]],
     "size": 20000000, "mtime": "2003-07-04T12:00:00Z", "media_type": "image/x-nikon-nef",
     "sha256": "0" * 63 + "1"},
    {"path": "../outside/escape.NEF", "size": 1},
]


def _message(subject: str, body: str, sender: str, when, attachment: tuple | None = None) -> EmailMessage:
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = sender, HOLDER, subject
    m["Date"] = format_datetime(when)
    m["Message-ID"] = f"<{hashlib.sha256(subject.encode()).hexdigest()[:12]}@example.org>"
    m.set_content(body)
    if attachment:
        name, data, mtype = attachment
        maintype, subtype = mtype.split("/", 1)
        m.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
    return m


def gmail_account() -> dict:
    from datetime import datetime, timezone

    acct = Account(HOLDER, 0x18D0000000000000, random.Random(11))
    t = datetime(2009, 4, 2, 10, 15, tzinfo=timezone.utc)
    acct.add(_message("Towpath survey notes", "The lock keeper counted forty narrowboats near the aqueduct.",
                      "surveyor@example.org", t), ["INBOX"])
    acct.add(_message("Photos from the aqueduct walk", "Raw files from the walk are attached.",
                      "walker@example.net", t.replace(year=2011, month=8, day=20),
                      ("DSC_0042.NEF", NEF_BYTES, "image/x-nikon-nef")), ["INBOX"])
    acct.add(_message("Canal society newsletter", "Spring meeting at the canal basin. Bring a flask.",
                      "news@society.example.org", t.replace(year=2010, month=3, day=1)), ["INBOX"])
    acct.add(_message("Re: lock gate paint", "Sent from the towpath, more soon.", HOLDER,
                      t.replace(year=2010, month=5, day=9)), ["SENT"])
    return acct.to_json()


def imap_mailboxes():
    """(mailbox, UIDVALIDITY, [(subject, body, sender, date, attachment)]) for the synthetic IMAP server."""
    return [
        ("INBOX", 1700000001, [
            ("Re: aqueduct restoration budget", "The restoration estimate is attached.", "treasurer@example.com",
             "Thu, 11 Feb 2010 09:00:00 +0000", ("budget.ods", b"synthetic spreadsheet", "application/octet-stream")),
            ("Photos for the club album", "Two raw files from the canal walk.", "photos@example.com",
             "Sat, 21 Aug 2011 10:00:00 +0000", ("DSC_0044.NEF", NEF_BYTES, "image/x-nikon-nef")),
        ]),
        ("Archive/2008", 1700000002, [
            ("Canal boat club minutes", "Minutes of the summer meeting. The towpath fundraiser went well.",
             "secretary@example.com", "Mon, 30 Jun 2008 20:00:00 +0000", None),
        ]),
    ]


CONFIG = """# Synthetic unified discovery environment. Generated; safe to delete.
[stores]
dir = "state"

[[sources]]
id = "gmail-fixture"
kind = "mail-provider"
adapter = "fixture-gmail"
path = "mail/account_u.json"
{imap}
[files]
recover_dir = "derived/recovered"

[[files.roots]]
alias = "archive"
path = "files/roots/archive"
exclude = ["private/**"]

[[files.roots]]
alias = "shared"
path = "files/roots/shared"

[[files.roots]]
alias = "photos"
path = "files/roots/photos"

[[files.providers]]
id = "fixture"
adapter = "fixture"
roots = ["archive", "shared"]
catalog = "files/catalog.json"

[[files.providers]]
id = "inventory"
adapter = "manifest"
roots = ["photos"]
manifests = ["files/manifests/photos.jsonl"]
"""

IMAP_SOURCE = """
[[sources]]
id = "imap-fixture"
kind = "mail-provider"
adapter = "imap"
host = "127.0.0.1"
port = {port}
security = "plain-loopback"
username = "reader@example.com"
password = "env:{env}"
timeout_seconds = 10
"""


def generate(out: Path, imap_port: int | None = None) -> dict:
    out = Path(out)
    (out / "mail").mkdir(parents=True, exist_ok=True)
    (out / "mail" / "account_u.json").write_text(json.dumps(gmail_account()))
    summary = corpus.generate(out / "files")
    (out / "files" / "roots" / "photos").mkdir(parents=True, exist_ok=True)
    (out / "files" / "manifests").mkdir(parents=True, exist_ok=True)
    (out / "files" / "manifests" / "photos.jsonl").write_text(
        "\n".join(json.dumps(line) for line in PHOTO_MANIFEST) + "\n")
    imap = IMAP_SOURCE.format(port=imap_port, env=IMAP_PASSWORD_ENV) if imap_port else ""
    (out / "towpath.toml").write_text(CONFIG.format(imap=imap))
    return {"out": str(out), "config": str(out / "towpath.toml"), "files": summary,
            "manifest_entries": len(PHOTO_MANIFEST) - 1, "imap": bool(imap_port)}
