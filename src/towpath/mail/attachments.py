"""Every attachment in an account's mail: what it is, who sent it, when, and how big, from the message structure the
connector already indexed (nothing more is read from the server).

A message filed in several folders (Gmail labels, an archive copy) counts once. Small pictures inside a message's
text (logos, signature images) are left out; pictures pasted into a message are kept.
"""

import re
import time
from contextlib import closing

from towpath.stores import open_store, store_path

INLINE_MIN_BYTES = 100_000  # a picture inside the message text counts from this size
KINDS = {
    "documents": ("Documents", ("application/pdf", "application/msword", "application/rtf", "text/rtf",
                                "application/vnd.openxmlformats-officedocument.wordprocessingml",
                                "application/vnd.openxmlformats-officedocument.presentationml",
                                "application/vnd.ms-powerpoint", "application/vnd.oasis.opendocument.text",
                                "application/vnd.apple.pages", "application/vnd.apple.keynote"),
                  ("pdf", "doc", "docx", "rtf", "odt", "pages", "ppt", "pptx", "key", "txt")),
    "spreadsheets": ("Spreadsheets", ("application/vnd.ms-excel", "text/csv", "application/vnd.oasis.opendocument."
                                      "spreadsheet", "application/vnd.openxmlformats-officedocument.spreadsheetml",
                                      "application/vnd.apple.numbers"), ("xls", "xlsx", "csv", "ods", "numbers")),
    "pictures": ("Pictures", ("image/",), ("jpg", "jpeg", "png", "gif", "heic", "tif", "tiff", "webp", "bmp")),
    "media": ("Audio and video", ("audio/", "video/"), ("mp3", "m4a", "wav", "mp4", "mov", "avi", "3gp")),
    "archives": ("Archives", ("application/zip", "application/x-zip", "application/gzip", "application/x-7z",
                              "application/x-rar", "application/x-tar"), ("zip", "gz", "tgz", "7z", "rar", "tar")),
    "calendar": ("Calendar invitations", ("text/calendar", "application/ics"), ("ics", "vcs")),
    "signatures": ("Signatures and keys", ("application/pgp", "application/pkcs7", "application/x-pkcs7"),
                   ("asc", "sig", "p7s")),
}
LABELS = {**{k: v[0] for k, v in KINDS.items()}, "other": "Other"}
ORDERS = {"newest": "date_utc DESC", "oldest": "date_utc", "largest": "size DESC"}
HIDDEN_BY_DEFAULT = ("signatures",)
SUMMARY_SECONDS = 600  # the summary reads every attachment row (seconds on a large account): kept this long
_summaries: dict[tuple, tuple[float, dict]] = {}


def kind_of(mime_type: str | None, filename: str | None) -> str:
    mime = (mime_type or "").lower()
    ext = (filename or "").rpartition(".")[2].lower() if "." in (filename or "") else ""
    for key, (_, prefixes, _) in KINDS.items():
        if any(mime.startswith(p) for p in prefixes):
            return key
    for key, (_, _, exts) in KINDS.items():
        if ext in exts:
            return key
    return "other"


def _rows(db, source_id: str, where: str = "", args: tuple = (), order: str = "date_utc DESC", limit: int = -1,
          offset: int = 0) -> list[dict]:
    db.create_function("towpath_kind", 2, kind_of, deterministic=True)
    return [dict(r) for r in db.execute(
        f"""SELECT * FROM (
              SELECT p.item_id, p.part_id, p.filename, p.mime_type, p.size, i.native_id, i.subject, i.from_addr,
                     i.date_utc, towpath_kind(p.mime_type, p.filename) AS kind
              FROM parts p JOIN items i USING (item_id)
              WHERE i.source_id = ? AND i.absent_since_run IS NULL AND (p.disposition = 'attachment' OR
                    (coalesce(p.filename, '') != '' AND coalesce(p.size, 0) >= {INLINE_MIN_BYTES}))
              GROUP BY coalesce(i.rfc_message_id, i.item_id), p.part_id)
            WHERE 1 {where} ORDER BY {order}, item_id, part_id LIMIT ? OFFSET ?""",
        (source_id, *args, limit, offset))]


def _filters(f: dict) -> tuple[str, tuple]:
    where, args = [], []
    if f.get("kind") in LABELS:
        where.append("kind = ?")
        args.append(f["kind"])
    else:
        where.append(f"kind NOT IN ({', '.join('?' for _ in HIDDEN_BY_DEFAULT)})")
        args += HIDDEN_BY_DEFAULT
    for word in re.findall(r"\S+", (f.get("q") or "").lower())[:6]:
        where.append("(lower(coalesce(filename, '')) LIKE ? OR lower(coalesce(subject, '')) LIKE ?)")
        args += [f"%{word}%", f"%{word}%"]
    if re.fullmatch(r"\d{4}", str(f.get("year") or "")):
        where.append("substr(date_utc, 1, 4) = ?")
        args.append(f["year"])
    if f.get("sender"):
        where.append("lower(coalesce(from_addr, '')) LIKE ?")
        args.append(f"%{f['sender'].lower()}%")
    return "".join(f" AND {w}" for w in where), tuple(args)


def listing(store_dir, source_id: str, f: dict, limit: int = 100, offset: int = 0, role: str = "web") -> list[dict]:
    if not store_path(store_dir, "source").exists():
        return []
    where, args = _filters(f)
    with closing(open_store(store_dir, "source", role)) as db:
        rows = _rows(db, source_id, where, args, ORDERS.get(f.get("order"), ORDERS["newest"]), limit, offset)
    for r in rows:
        r["ref"] = f"{source_id}:{r['native_id']}#part={r['part_id']}"
        r["label"] = LABELS[r["kind"]]
    return rows


def of_message(store_dir, source_id: str, item_id: str, role: str = "web") -> list[dict]:
    """One message's attachments (signatures and logos included: the owner is looking at that message)."""
    if not store_path(store_dir, "source").exists():
        return []
    with closing(open_store(store_dir, "source", role)) as db:
        rows = [dict(r) for r in db.execute(
            "SELECT p.part_id, p.filename, p.mime_type, p.size, i.native_id FROM parts p JOIN items i USING (item_id) "
            "WHERE p.item_id = ? AND i.source_id = ? AND (p.disposition = 'attachment' OR coalesce(p.filename, '') "
            "!= '') ORDER BY p.part_id", (item_id, source_id))]
    for r in rows:
        r["ref"] = f"{source_id}:{r['native_id']}#part={r['part_id']}"
        r["label"] = LABELS[kind_of(r["mime_type"], r["filename"])]
    return rows


def summary(store_dir, source_id: str, role: str = "web") -> dict:
    """Counts and space by kind and by year, and the senders who sent the most (computed at most every ten
    minutes per account)."""
    if not store_path(store_dir, "source").exists():
        return {"kinds": [], "years": [], "senders": [], "total": (0, 0)}
    key = (str(store_dir), source_id)
    if key in _summaries and time.monotonic() - _summaries[key][0] < SUMMARY_SECONDS:
        return _summaries[key][1]
    with closing(open_store(store_dir, "source", role)) as db:
        rows = _rows(db, source_id)
    kinds: dict[str, list] = {}
    years: dict[str, list] = {}
    senders: dict[str, list] = {}
    for r in rows:
        for bucket, key in ((kinds, r["kind"]), (years, (r["date_utc"] or "")[:4] or "?"),
                            (senders, r["from_addr"] or "?")):
            got = bucket.setdefault(key, [0, 0])
            got[0] += 1
            got[1] += r["size"] or 0
    found = {"kinds": [(k, LABELS[k], *v) for k, v in sorted(kinds.items(), key=lambda x: -x[1][1])],
             "years": sorted(((y, *v) for y, v in years.items()), reverse=True),
             "senders": [(s, *v) for s, v in sorted(senders.items(), key=lambda x: -x[1][1])[:15]],
             "total": (len(rows), sum(r["size"] or 0 for r in rows))}
    _summaries[key] = (time.monotonic(), found)
    return found
