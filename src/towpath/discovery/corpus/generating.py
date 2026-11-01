"""Writing the synthetic corpus, its fixture catalog and an example config."""

import base64
import io
import json
import os
import zipfile
from pathlib import Path

from towpath.discovery.corpus.pieces import (
    DOCX_TYPE,
    FIXED_MTIME,
    INJECTION_TEXT,
    PAPER_TEXT,
    TEXT_LIMIT,
    ZIP_DATE,
    docx,
    encrypted_zip,
    _mbox_bytes,
    _message,
    _sha256,
)


def generate(out: Path) -> dict:
    """Write the corpus and ``catalog.json`` under ``out``; return a summary."""
    out = Path(out)
    archive, shared, outside = out / "roots" / "archive", out / "roots" / "shared", out / "outside"
    for d in (archive / "2003", archive / "long", archive / "locked", archive / "broken", archive / "outlook",
              archive / "private", archive / "tricky", archive / "notes", shared, outside):
        d.mkdir(parents=True, exist_ok=True)

    paper = docx(PAPER_TEXT)
    messages = [
        _message(1, "Final paper", "Mon, 05 May 2003 10:00:00 +0000", "Attached is my final paper.",
                 ("paper.docx", paper, DOCX_TYPE)),
        _message(2, "Fwd: Final paper", "Tue, 06 May 2003 09:30:00 +0000", "Forwarding my paper again.",
                 ("paper.docx", paper, DOCX_TYPE)),
        _message(3, "Lunch", "Wed, 07 May 2003 12:00:00 +0000", "Lunch on Friday at the canal cafe?", None),
    ]
    mbox = _mbox_bytes(messages, out)
    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(zipfile.ZipInfo("mail/backup.mbox", ZIP_DATE), mbox)
    files = {
        "2003/old-mail.zip": zbuf.getvalue(),
        "long/thesis-notes.txt": ("Thesis notes. " + "towpath lock keeper ledger " * 2000
                                  + "\nThe final line mentions a kingfisher.\n").encode(),
        "locked/secret.zip": encrypted_zip("secret.txt", b"zebrafinch inside an encrypted archive\n"),
        "private/diary.txt": b"Private diary. zebrafinch sighting by the canal.\n",
        "notes/injection.md": INJECTION_TEXT.encode(),
        "outlook/old.pst": b"!BDN" + bytes(508),
    }
    tbuf = io.BytesIO()
    with zipfile.ZipFile(tbuf, "w") as z:
        z.writestr(zipfile.ZipInfo("../../evil.txt", ZIP_DATE), "zebrafinch traversal member\n")
        z.writestr(zipfile.ZipInfo("/abs.txt", ZIP_DATE), "zebrafinch absolute member\n")
    files["tricky/traversal.zip"] = tbuf.getvalue()
    good = files["2003/old-mail.zip"]
    files["broken/corrupt.zip"] = good[: len(good) // 2]

    for rel, data in files.items():
        (archive / rel).write_bytes(data)
        os.utime(archive / rel, (FIXED_MTIME, FIXED_MTIME))
    (shared / "copy-of-paper.docx").write_bytes(paper)
    os.utime(shared / "copy-of-paper.docx", (FIXED_MTIME, FIXED_MTIME))
    (outside / "secret.txt").write_text("outside the root: zebrafinch must never appear\n")
    link = archive / "escape-link"
    if not link.is_symlink():
        link.symlink_to(Path("..") / ".." / "outside" / "secret.txt")

    catalog = _catalog(files, paper)
    for entry in catalog["entries"]:
        entry["source"] = _stamp(out / "roots" / entry["root"] / entry["path"])
    (out / "catalog.json").write_text(json.dumps(catalog, indent=1, sort_keys=True))
    return {"out": str(out), "files": len(files) + 2, "catalog_entries": len(catalog["entries"]),
            "paper_sha256": _sha256(paper)}


def _stamp(path: Path) -> dict | None:
    """The outer file as the fixture "index" records it, like a real provider at indexing time."""
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return None
    return {"size": st.st_size, "mtime": int(st.st_mtime), "ctime": int(st.st_ctime), "basis": "fixture catalog"}


def reindex(out: Path) -> dict:
    """Simulate re-running the provider: re-stamp catalog entries and drop those whose file is gone.

    Extracted text in the catalog is not refreshed; tests that need re-extraction use Recoll.
    """
    path = Path(out) / "catalog.json"
    catalog = json.loads(path.read_text())
    kept = []
    for entry in catalog["entries"]:
        stamp = _stamp(Path(out) / "roots" / entry["root"] / entry["path"])
        if stamp is not None:
            entry["source"] = stamp
            kept.append(entry)
    dropped = len(catalog["entries"]) - len(kept)
    catalog["entries"] = kept
    path.write_text(json.dumps(catalog, indent=1, sort_keys=True))
    return {"entries": len(kept), "dropped": dropped}


def _file_dates() -> list[dict]:
    return [{"meaning": "file-modified", "value": "2004-01-02T00:00:00Z", "basis": "filesystem mtime"}]


def _entry(native_id, root, path, members=(), media_type=None, size=None, status="indexed", text=None,
           dates=None, hashes=None, recover=None, detail=None, limit=TEXT_LIMIT):
    extracted = None if text is None else len(text.encode())
    entry = {
        "native_id": native_id, "root": root, "path": path, "members": [list(m) for m in members],
        "media_type": media_type, "size": size, "dates": dates or _file_dates(), "hashes": hashes or {},
        "extraction": {"status": status, "parser": "fixture", "parser_version": "1",
                       "extracted_bytes": extracted, "limit_bytes": limit if text is not None else None,
                       "detail": detail},
        "text": text,
    }
    if recover is not None:
        entry["recover_b64"] = base64.b64encode(recover).decode()
    return entry


def _catalog(files: dict, paper: bytes) -> dict:
    """What an ideal provider would report about the corpus. A test aid, not a provider's real output."""
    zipped = ("archive-member", "mail/backup.mbox", None)
    paper_dates = [{"meaning": "member-modified", "value": "2004-01-01T12:00:00", "basis": "ZIP member date"}]
    long_text = files["long/thesis-notes.txt"].decode()[:TEXT_LIMIT]
    e = [
        _entry("fx-1", "archive", "2003/old-mail.zip", (), "application/zip", len(files["2003/old-mail.zip"]),
               text="", hashes={"sha256": _sha256(files["2003/old-mail.zip"])}),
    ]
    for n, (subject, date, body) in enumerate((
        ("Final paper", "2003-05-05T10:00:00+00:00", "Attached is my final paper."),
        ("Fwd: Final paper", "2003-05-06T09:30:00+00:00", "Forwarding my paper again."),
        ("Lunch", "2003-05-07T12:00:00+00:00", "Lunch on Friday at the canal cafe?"),
    ), start=1):
        msg_dates = [{"meaning": "message-date", "value": date, "basis": "Date header"}]
        e.append(_entry(f"fx-msg-{n}", "archive", "2003/old-mail.zip", (zipped, ("mail-message", None, n)),
                        "message/rfc822", None, text=f"Subject: {subject}\n\n{body}", dates=msg_dates + paper_dates))
        if n < 3:
            e.append(_entry(f"fx-att-{n}", "archive", "2003/old-mail.zip",
                            (zipped, ("mail-message", None, n), ("attachment", "paper.docx", 0)), DOCX_TYPE,
                            len(paper), text=PAPER_TEXT, dates=msg_dates + paper_dates,
                            hashes={"sha256": _sha256(paper)}, recover=paper))
    e += [
        _entry("fx-long", "archive", "long/thesis-notes.txt", (), "text/plain",
               len(files["long/thesis-notes.txt"]), status="truncated", text=long_text,
               hashes={"sha256": _sha256(files["long/thesis-notes.txt"])},
               detail=f"text cut at the fixture limit of {TEXT_LIMIT} bytes"),
        _entry("fx-locked", "archive", "locked/secret.zip", (), "application/zip",
               len(files["locked/secret.zip"]), status="encrypted", detail="member is encrypted; no passphrase"),
        _entry("fx-corrupt", "archive", "broken/corrupt.zip", (), "application/zip",
               len(files["broken/corrupt.zip"]), status="failed", detail="archive is truncated"),
        _entry("fx-pst", "archive", "outlook/old.pst", (), "application/vnd.ms-outlook",
               len(files["outlook/old.pst"]), status="unsupported", detail="needs a PST helper"),
        _entry("fx-diary", "archive", "private/diary.txt", (), "text/plain", len(files["private/diary.txt"]),
               text=files["private/diary.txt"].decode()),
        _entry("fx-inject", "archive", "notes/injection.md", (), "text/markdown",
               len(files["notes/injection.md"]), text=INJECTION_TEXT,
               hashes={"sha256": _sha256(files["notes/injection.md"])}),
        _entry("fx-trav-1", "archive", "tricky/traversal.zip", (("archive-member", "../../evil.txt", None),),
               "text/plain", None, text="zebrafinch traversal member\n", recover=b"zebrafinch traversal member\n"),
        _entry("fx-trav-2", "archive", "tricky/traversal.zip", (("archive-member", "/abs.txt", None),),
               "text/plain", None, text="zebrafinch absolute member\n"),
        _entry("fx-escape", "archive", "escape-link", (), "text/plain", None,
               text="outside the root: zebrafinch must never appear\n"),
        _entry("fx-shared", "shared", "copy-of-paper.docx", (), DOCX_TYPE, len(paper), text=PAPER_TEXT,
               hashes={"sha256": _sha256(paper)}),
    ]
    return {"format": "towpath.files.fixture-catalog/1", "text_limit": TEXT_LIMIT, "entries": e}


EXAMPLE_CONFIG = """\
# Synthetic file discovery corpus. Generated; safe to delete.
[stores]
dir = "state"

[files]
recover_dir = "derived/recovered"

[[files.roots]]
alias = "archive"
path = "roots/archive"
exclude = ["private/**"]

[[files.roots]]
alias = "shared"
path = "roots/shared"

[[files.providers]]
id = "fixture"
adapter = "fixture"
roots = ["archive", "shared"]
catalog = "catalog.json"
"""


def write_example_config(out: Path) -> Path:
    path = Path(out) / "towpath.toml"
    path.write_text(EXAMPLE_CONFIG)
    return path
