"""Dated documents as life evidence: a diploma, a lease, a deed, an offer letter.

In the folders the owner allowed on the Life page, a document whose name says what it is and when ("Lease
2014-08-01.pdf", "2009/Diploma.pdf") becomes a proposed event ("Signed a lease"), citing the file and dated by its
name or its year folder; a document with no date in its name or folders is left alone rather than dated by when the
file was last changed. Only names are read: no model, and nothing inside a file. Identity papers (passports,
licences) are never proposed, and the private export is never looked at.
"""

import re
from contextlib import closing
from datetime import date, datetime, timedelta, timezone

from towpath import life
from towpath.photos import private_in
from towpath.stores import open_store, store_path
from towpath.unified.claims import Citation, Claim, ClaimError, When

RUN = "documents"
RECHECK = timedelta(hours=6)
STATE_KEY = "documents"
DOCUMENTS = {".pdf", ".doc", ".docx", ".odt", ".rtf", ".pages", ".txt"}
# (what a name says, what the event is, the search words that find it)
KINDS = (
    (re.compile(r"\bdiploma\b|\bgraduation\b|\bdegree certificate\b", re.I), "Graduated", "accomplishment",
     ("diploma", "graduation", "degree certificate")),
    (re.compile(r"\blease\b|\brental agreement\b|\btenancy agreement\b", re.I), "Signed a lease", "event",
     ("lease", "rental agreement", "tenancy agreement")),
    (re.compile(r"\bdeed\b|\bclosing disclosure\b|\bsettlement statement\b|\bhud[- ]?1\b", re.I), "Bought a home",
     "event", ("deed", "closing disclosure", "settlement statement", "hud")),
    (re.compile(r"\bmarriage (certificate|license|licence)\b", re.I), "Got married", "event", ("marriage",)),
    (re.compile(r"\boffer letter\b|\bemployment (agreement|contract)\b", re.I), "Took a new job", "event",
     ("offer letter", "employment agreement", "employment contract")),
    (re.compile(r"\bresignation\b", re.I), "Left a job", "event", ("resignation",)),
    (re.compile(r"\bbill of sale\b|\b(vehicle|car) title\b", re.I), "Bought a car", "event",
     ("bill of sale", "vehicle title", "car title")),
    (re.compile(r"\bcertificate of (completion|achievement)\b|\bcertification\b", re.I), "Earned a certificate",
     "accomplishment", ("certificate of", "certification")),
)
NEVER = re.compile(r"passport|licen[cs]e(?! agreement)|social security|\bssn\b|birth certificate|\bvisa\b|"
                   r"\bid\b|\bid card|identity|identification", re.I)
DAY = re.compile(r"(?<!\d)((?:19|20)\d{2})[-_. ]?(0[1-9]|1[0-2])[-_. ]?(0[1-9]|[12]\d|3[01])(?!\d)")
MONTH = re.compile(r"(?<!\d)((?:19|20)\d{2})[-_. ](0[1-9]|1[0-2])(?!\d)")
YEAR = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")


def _words(name: str) -> str:
    return re.sub(r"[_\-.]+", " ", name)


def kind_of(rel_path: str) -> tuple[str, str] | None:
    """(statement, claim kind) for a document (by its name; never anything in a folder of identity papers)."""
    folders, _, name = rel_path.rpartition("/")
    stem, dot, ext = name.rpartition(".")
    if not dot or f".{ext.lower()}" not in DOCUMENTS:
        return None
    words = _words(stem)
    if NEVER.search(words) or NEVER.search(_words(folders)):
        return None
    return next(((statement, kind) for pattern, statement, kind, _ in KINDS if pattern.search(words)), None)


def when_of(rel_path: str) -> When | None:
    """The date a document's name gives (else its nearest year folder), or None."""
    parts = rel_path.split("/")
    name = parts[-1].rpartition(".")[0] or parts[-1]
    today = date.today()
    for pattern, precision in ((DAY, "day"), (MONTH, "month")):
        m = pattern.search(name)
        if m:
            try:
                first = date(int(m[1]), int(m[2]), int(m[3]) if precision == "day" else 1)
            except ValueError:
                continue
            if first > today:
                return None  # a date to come: not something that happened
            if precision == "day":
                return When(first.isoformat(), "day", first.isoformat(), first.isoformat())
            last = (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
            return When(first.isoformat()[:7], "month", first.isoformat(), last.isoformat())
    for part in [name, *reversed(parts[:-1])]:
        years = YEAR.findall(part)  # the last one: "Lease 1920 Maple Ave 2014" is from 2014
        if years and int(years[-1]) <= today.year:
            return When(years[-1], "year", f"{years[-1]}-01-01", f"{years[-1]}-12-31")
    return None


def _match() -> str:
    """The catalog's name index matches substrings (trigrams): each phrase as written with spaces, underscores,
    dashes or nothing between its words."""
    phrases = {w.replace(" ", sep) for *_, words in KINDS for w in words for sep in (" ", "_", "-", ".", "")}
    return " OR ".join(f'"{p}"' for p in sorted(phrases))


def propose(config) -> list[dict]:
    from towpath.unified import grants

    allowed = sorted(sid for sid, f in grants.granted(config.store_dir).items() if "life-evidence" in f)
    if not allowed or not store_path(config.store_dir, "files").exists():
        return []
    with closing(open_store(config.store_dir, "files", "worker")) as files:
        top = files.execute("SELECT max(rowid) FROM occurrences").fetchone()[0] or 0
        mark = f"{','.join(allowed)}@{top}"
        with closing(open_store(config.store_dir, "life", life.WRITER)) as db:
            row = db.execute("SELECT digest, updated_at FROM group_runs WHERE key = ?", (STATE_KEY,)).fetchone()
        now = datetime.now(timezone.utc)
        # Looked at again only once the catalog changed, and at most every few hours while it keeps changing.
        if row and (row[0] == mark or now - datetime.fromisoformat(row[1]) < RECHECK):
            return []
        marks = ",".join("?" for _ in allowed)
        ends = " OR ".join("lower(o.rel_path) LIKE ?" for _ in DOCUMENTS)
        found = files.execute(
            f"SELECT o.provider_id, o.occurrence_id, o.root_alias, o.rel_path FROM occurrences_text t JOIN occurrences "
            f"o ON o.rowid = t.rowid WHERE occurrences_text MATCH ? AND o.missing_since_run IS NULL AND "
            f"o.provider_id IN ({marks}) AND ({ends})",
            (_match(), *allowed, *(f"%{e}" for e in sorted(DOCUMENTS))))
        # the private export is tested on the full path before a name is even looked at
        candidates = [(p, o, a, r) for p, o, a, r in found if not private_in(config, a, r) and kind_of(r)]
    made = 0
    with closing(open_store(config.store_dir, "life", life.WRITER)) as db:
        for provider, occ, _alias, rel in candidates:
            name = rel.rpartition("/")[2]
            what, when = kind_of(rel), when_of(rel)
            if what is None or when is None:
                continue
            ref = f"{provider}:{occ}"
            try:
                claim = Claim(life.claim_id(what[0], "occurred", [ref]), what[1], what[0], "occurred", when,
                              (Citation(ref=ref, captured_excerpt=name),), "documents/1", significance="milestone")
            except ClaimError:
                continue
            made += life.save(db, claim, provider, RUN)
        db.execute("INSERT OR REPLACE INTO group_runs (key, digest, updated_at) VALUES (?,?,?)",
                   (STATE_KEY, mark, now.isoformat(timespec="seconds")))
        db.commit()
    return [{"step": "life-documents", "proposed": made}] if made else []
