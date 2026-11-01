"""The two synthetic accounts and the destination folders."""

import base64
import hashlib
import json
import random
from email.mime.application import MIMEApplication
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import timedelta
from pathlib import Path

from towpath.fixtures.generator.pieces import (
    HOLDER_A,
    HOLDER_B,
    INJECTION_TEXT,
    Account,
    _headers,
    synthetic_pdf,
    synthetic_png,
    _text,
    _with_attachment,
)


def build_account_a(rng: random.Random) -> tuple[Account, dict]:
    acct = Account(HOLDER_A, 0x18C0000000000000, rng)
    known: dict = {"statements": {}, "photos": {}}
    when = lambda: acct.clock + timedelta(hours=1)  # noqa: E731

    for month in range(1, 7):
        pdf = synthetic_pdf(f"statement 2026-{month:02d}", rng)
        name = f"statement-2026-{month:02d}.pdf"
        msg = _headers(_with_attachment(f"Your statement for month {month} is attached.", pdf, name, "pdf"),
                       "Example Bank <statements@bank.example.com>", HOLDER_A,
                       f"Statement for 2026-{month:02d}", when(), f"<stmt-{month:02d}@bank.example.com>")
        known["statements"][name] = (acct.add(msg, ["INBOX", "CATEGORY_UPDATES"]), pdf)

    for i in range(4):
        png = synthetic_png(rng)
        name = f"photo-{i + 1}.png"
        msg = _headers(_with_attachment("A photo from the weekend.", png, name, "png"),
                       "Jordan Sample <jordan@family.test>", HOLDER_A, f"Weekend photo {i + 1}",
                       when(), f"<photo-{i + 1}@family.test>")
        known["photos"][name] = (acct.add(msg, ["INBOX", "CATEGORY_PERSONAL"]), png)

    related = MIMEMultipart("related")
    related.attach(MIMEText('<p>Logo below</p><img src="cid:logo">', "html", "utf-8"))
    logo = MIMEImage(synthetic_png(rng), "png")
    logo.add_header("Content-ID", "<logo>")
    logo.add_header("Content-Disposition", "inline", filename="logo.png")
    related.attach(logo)
    acct.add(_headers(related, "Example Shop <hello@shop.example.net>", HOLDER_A, "Spring catalog",
                      when(), "<catalog@shop.example.net>"), ["INBOX", "CATEGORY_PROMOTIONS"])

    big = rng.randbytes(1_500_000)
    acct.add(_headers(_with_attachment("Project files attached.", big, "project-archive.zip", "zip"),
                      "Casey Demo <casey@work.example.org>", HOLDER_A, "Project archive", when(),
                      "<archive@work.example.org>"), ["INBOX"])

    small_pdf = synthetic_pdf("small receipt", rng, pad=200)
    acct.inline_data_files.add("receipt-small.pdf")
    known["inline_pdf"] = (acct.add(_headers(
        _with_attachment("Receipt attached.", small_pdf, "receipt-small.pdf", "pdf"),
        "Corner Cafe <receipts@cafe.example.com>", HOLDER_A, "Your receipt", when(),
        "<receipt@cafe.example.com>"), ["INBOX", "CATEGORY_UPDATES"]), small_pdf)

    deep_pdf = synthetic_pdf("deeply nested invoice", rng)
    level4 = MIMEMultipart("mixed")
    level4.attach(_text("Invoice details."))
    invoice = MIMEApplication(deep_pdf, "pdf")
    invoice.add_header("Content-Disposition", "attachment", filename="invoice-nested.pdf")
    level4.attach(invoice)
    level3 = MIMEMultipart("alternative")
    level3.attach(_text("Plain version."))
    level3.attach(level4)
    level2 = MIMEMultipart("related")
    level2.attach(level3)
    level1 = MIMEMultipart("mixed")
    level1.attach(level2)
    known["deep_pdf"] = (acct.add(_headers(level1, "Billing <billing@utility.example.org>", HOLDER_A,
                                           "Invoice (nested)", when(), "<invoice@utility.example.org>"),
                                  ["INBOX"]), deep_pdf)

    for i in range(8):
        msg = MIMEMultipart("alternative")
        msg.attach(_text(f"Issue {i + 1} of the weekly digest."))
        msg.attach(MIMEText(f"<p>Issue {i + 1}</p>", "html", "utf-8"))
        acct.add(_headers(msg, "Weekly Digest <digest@news.example.com>", HOLDER_A, f"Weekly digest #{i + 1}",
                          when(), f"<digest-{i + 1}@news.example.com>",
                          {"List-Id": "<weekly.news.example.com>",
                           "List-Unsubscribe": "<https://news.example.com/unsub>, <mailto:unsub@news.example.com>",
                           "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}),
                 ["INBOX", "CATEGORY_PROMOTIONS", "UNREAD"])

    for t in range(3):
        first = acct.add(_headers(_text(f"Are you free on day {t + 3}?"), "Morgan Test <morgan@friends.test>",
                                  HOLDER_A, f"Plans {t + 1}", when(), f"<plans-{t + 1}a@friends.test>"),
                         ["INBOX", "CATEGORY_PERSONAL"])
        acct.add(_headers(_text("Yes, that works."), HOLDER_A, "morgan@friends.test", f"Re: Plans {t + 1}",
                          when(), f"<plans-{t + 1}b@home.example.com>"), ["SENT"], thread=first)

    for i in range(5):
        acct.add(_headers(_text(f"Your login code is {100000 + i}."), "Service <no-reply@service.example.net>",
                          HOLDER_A, "Sign-in code", when(), f"<code-{i}@service.example.net>"),
                 ["INBOX", "CATEGORY_UPDATES"])

    known["shared_message_id"] = "<club-news@club.example.org>"
    acct.add(_headers(_text("Club meeting next week."), "Club <club@club.example.org>", HOLDER_A,
                      "Club meeting", when(), known["shared_message_id"]), ["INBOX"])

    known["no_message_id"] = acct.add(_headers(_text("No identifier on this one."), "Old System <legacy@old.test>",
                                               HOLDER_A, "Legacy notice", when(), None), ["INBOX"])
    bad_date = _headers(_text("Date header is broken."), "Clock <clock@old.test>", HOLDER_A, "Broken date",
                        None, "<broken-date@old.test>")
    bad_date["Date"] = "sometime on a thursday"
    known["bad_date"] = acct.add(bad_date, ["INBOX"])
    latin = MIMEText("Café menu: crème brûlée", "plain", "iso-8859-1")
    known["latin1"] = acct.add(_headers(latin, "Cafe <menu@cafe.example.com>", HOLDER_A,
                                        "=?iso-8859-1?q?Men=FC_du_jour?=", when(), "<menu@cafe.example.com>"),
                               ["INBOX"])

    injection_pdf = synthetic_pdf("instructions", rng)
    known["injection"] = acct.add(_headers(_with_attachment(INJECTION_TEXT, injection_pdf, "instructions.pdf", "pdf"),
                                           "Admin <admin@phish.example.net>", HOLDER_A, "Urgent account notice",
                                           when(), "<urgent@phish.example.net>"), ["INBOX"])
    return acct, known


def build_account_b(rng: random.Random, shared_message_id: str) -> Account:
    acct = Account(HOLDER_B, 0x19D0000000000000, rng)
    when = lambda: acct.clock + timedelta(hours=1)  # noqa: E731
    acct.add(_headers(_text("Club meeting next week."), "Club <club@club.example.org>", HOLDER_B,
                      "Club meeting", when(), shared_message_id), ["INBOX"])
    for i in range(4):
        acct.add(_headers(_text(f"Deals {i + 1}"), "Deals <deals@shop.example.net>", HOLDER_B, f"Deals {i + 1}",
                          when(), f"<deals-{i}@shop.example.net>"), ["INBOX", "CATEGORY_PROMOTIONS"])
    for i in range(3):
        acct.add(_headers(_text(f"Note {i + 1}"), "Sam Placeholder <sam@friends.test>", HOLDER_B, f"Note {i + 1}",
                          when(), f"<note-{i}@friends.test>"), ["INBOX", "CATEGORY_PERSONAL"])
    acct.add(_headers(_with_attachment("Picture.", synthetic_png(rng), "garden.png", "png"),
                      "Sam Placeholder <sam@friends.test>", HOLDER_B, "Garden", when(), "<garden@friends.test>"),
             ["INBOX"])
    acct.add(_headers(_text("Reminder."), "Service <no-reply@service.example.net>", HOLDER_B, "Reminder",
                      when(), "<reminder@service.example.net>"), ["INBOX"])
    return acct


def _write_folder(folder: Path, algorithm: str, files: dict[str, bytes], rng: random.Random) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    entries = []
    for name, data in sorted(files.items()):
        (folder / name).write_bytes(data)
        if algorithm == "sha256":
            checksum = hashlib.sha256(data).hexdigest()
        else:
            checksum = base64.b64encode(hashlib.sha1(data).digest()).decode("ascii")
        entries.append({"path": name, "checksum": checksum, "size": len(data)})
    manifest = {"schema": "towpath.fixture.folder/0", "algorithm": algorithm, "files": entries}
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2))
