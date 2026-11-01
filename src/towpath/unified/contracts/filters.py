"""Search filters: parsed from a query's words, checked, and matched against results."""

import re
from dataclasses import dataclass
from datetime import datetime

from towpath.unified.contracts.base import (
    DATE_MEANINGS,
    EVIDENCE_DATES,
    RESULT_KINDS,
    ContractError,
    _one_of,
    _parse_iso,
    check_source_id,
)
from towpath.unified.contracts.results import Result

FILTER_KEYS = {"source", "kind", "ext", "extension", "type", "name", "after", "before", "date", "in", "size",
               "modified"}
SIZE_UNITS = {"": 1, "b": 1, "kb": 1000, "k": 1000, "mb": 1000**2, "m": 1000**2, "gb": 1000**3, "g": 1000**3,
              "tb": 1000**4}
TOKENS = re.compile(r'(\w+:"[^"]*"|"[^"]*"|\S+)')


def parse_size(text: str) -> int:
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([a-zA-Z]*)", text.strip())
    if not m or m.group(2).lower() not in SIZE_UNITS:
        raise ContractError(f"size {text!r} is a number with an optional unit (KB, MB, GB)")
    return int(float(m.group(1)) * SIZE_UNITS[m.group(2).lower()])


def size_bounds(value: str) -> tuple[int | None, int | None]:
    """``>10MB``, ``<1KB``, ``10MB..2GB`` or ``10MB`` (at least) as byte bounds."""
    if ".." in value:
        low, high = value.split("..", 1)
        return (parse_size(low) if low else None), (parse_size(high) if high else None)
    if value.startswith((">", "<")):
        bound = parse_size(value.lstrip("<>="))
        return (bound, None) if value.startswith(">") else (None, bound)
    return parse_size(value), None


def month_bounds(value: str) -> tuple[str, str]:
    """``2019`` or ``2019-05`` as first and last day."""
    import calendar

    if re.fullmatch(r"\d{4}", value):
        return f"{value}-01-01", f"{value}-12-31"
    m = re.fullmatch(r"(\d{4})-(\d{2})", value)
    if not m or not 1 <= int(m.group(2)) <= 12:
        raise ContractError(f"modified {value!r} is a year (2019) or a month (2019-05)")
    last = calendar.monthrange(int(m.group(1)), int(m.group(2)))[1]
    return f"{value}-01", f"{value}-{last:02d}"


@dataclass(frozen=True)
class Filters:
    """A keyword query plus typed metadata filters.

    ``text`` is keyword text; empty means a metadata-only query (for example ``extension:nef``),
    which every source can answer from its catalog without text extraction. Dates compare by
    calendar day against the result's evidence dates (never process dates unless asked for).
    """

    text: str = ""
    sources: tuple[str, ...] = ()
    kinds: tuple[str, ...] = ()
    extensions: tuple[str, ...] = ()
    media_types: tuple[str, ...] = ()
    name: str | None = None
    after: str | None = None
    before: str | None = None
    date_meanings: tuple[str, ...] = EVIDENCE_DATES
    within: str | None = None  # ``in:"root/folder"``: a root alias, or a folder inside one
    min_size: int | None = None
    max_size: int | None = None

    def __post_init__(self):
        for source_id in self.sources:
            check_source_id(source_id)
        for kind in self.kinds:
            _one_of(kind, RESULT_KINDS, "result kind")
        for meaning in self.date_meanings:
            _one_of(meaning, DATE_MEANINGS, "date meaning")
        for ext in self.extensions:
            if not re.fullmatch(r"[a-z0-9]{1,16}", ext):
                raise ContractError(f"extension {ext!r} must be 1-16 lowercase letters or digits, without a dot")
        for bound in (self.after, self.before):
            if bound is not None:
                _parse_iso(bound)
        if len(self.text) > 512:
            raise ContractError("query text is limited to 512 characters")
        if not self.text.strip() and not (self.extensions or self.media_types or self.name or self.kinds
                                          or self.after or self.before or self.within or self.min_size is not None
                                          or self.max_size is not None):
            raise ContractError("give query text or at least one metadata filter")

    @property
    def metadata_only(self) -> bool:
        return not self.text.strip()

    @property
    def words(self) -> list[str]:
        return [w.lower() for w in self.text.split() if w]

    @classmethod
    def parse(cls, query: str, **extra) -> "Filters":
        """``extension:nef kind:file after:2004-01-01 canal`` -> typed filters plus keyword text."""
        fields: dict = {"sources": [], "kinds": [], "extensions": [], "media_types": []}
        words = []
        for token in TOKENS.findall(query or ""):
            key, sep, value = token.partition(":")
            value = value.strip('"')
            if not sep or key.lower() not in FILTER_KEYS or not value:
                words.append(token.strip('"'))
                continue
            key = key.lower()
            if key == "in":
                fields["within"] = value.strip("/")
                continue
            if key == "size":
                fields["min_size"], fields["max_size"] = size_bounds(value)
                continue
            if key == "modified":
                fields["after"], fields["before"] = month_bounds(value)
                fields["date_meanings"] = ("file-modified", "member-modified", "document-modified")
                continue
            if key == "source":
                fields["sources"].append(value)
            elif key == "kind":
                fields["kinds"].append(value)
            elif key in {"ext", "extension"}:
                fields["extensions"].append(value.lower().lstrip("."))
            elif key == "type":
                fields["media_types"].append(value.lower())
            elif key == "date":
                fields["after"] = fields["before"] = value
            else:
                fields[key] = value
        fields["text"] = " ".join(words)
        for key, value in extra.items():
            if value in (None, (), []):
                continue
            if isinstance(value, (list, tuple)):
                fields[key] = list(fields.get(key, [])) + list(value)
            else:
                fields[key] = value
        return cls(**{k: tuple(v) if isinstance(v, list) else v for k, v in fields.items()})

    def to_query(self) -> str:
        """The filter syntax that parses back to these filters (sources excluded)."""
        parts = [f"kind:{k}" for k in self.kinds] + [f"extension:{e}" for e in self.extensions]
        parts += [f"type:{m}" for m in self.media_types]
        parts += [f"name:{self.name}"] if self.name else []
        parts += [f"after:{self.after}"] if self.after else []
        parts += [f"before:{self.before}"] if self.before else []
        parts += [f'in:"{self.within}"'] if self.within else []
        if self.min_size is not None or self.max_size is not None:
            parts.append(f"size:{self.min_size or ''}..{self.max_size if self.max_size is not None else ''}")
        return " ".join(parts + ([self.text] if self.text else []))

    def to_dict(self) -> dict:
        return {"text": self.text, "sources": list(self.sources), "kinds": list(self.kinds),
                "extensions": list(self.extensions), "media_types": list(self.media_types), "name": self.name,
                "after": self.after, "before": self.before, "date_meanings": list(self.date_meanings),
                "within": self.within, "min_size": self.min_size, "max_size": self.max_size}

    @classmethod
    def from_dict(cls, data: dict) -> "Filters":
        known = {"text", "sources", "kinds", "extensions", "media_types", "name", "after", "before", "date_meanings",
                 "within", "min_size", "max_size"}
        unknown = set(data) - known
        if unknown:
            raise ContractError(f"unknown filter field(s): {', '.join(sorted(unknown))}")
        return cls(**{k: tuple(v) if isinstance(v, list) else v for k, v in data.items()})

    def accepts_metadata(self, result: "Result") -> bool:
        """Whether a result passes every typed filter (keyword text is the adapter's job)."""
        if self.kinds and result.kind not in self.kinds:
            return False
        if self.extensions and result.extension not in self.extensions:
            return False
        if self.media_types and not any((result.media_type or "").startswith(m) for m in self.media_types):
            return False
        if self.name and self.name.lower() not in (result.title or "").lower():
            return False
        if self.min_size is not None and (result.size is None or result.size < self.min_size):
            return False
        if self.max_size is not None and (result.size is None or result.size > self.max_size):
            return False
        if self.within:
            loc = result.locator
            here = f"{loc['root']}/{loc.get('path') or ''}" if loc.get("root") else (loc.get("mailbox") or "")
            if not (here == self.within or here.startswith(self.within + "/")):
                return False
        if self.after or self.before:
            days = [d.day() for d in result.dates if d.meaning in self.date_meanings]
            days = [d for d in days if d]
            low = self.after and _parse_iso(self.after)
            high = self.before and _parse_iso(self.before)
            low = low.date().isoformat() if isinstance(low, datetime) else (low.isoformat() if low else None)
            high = high.date().isoformat() if isinstance(high, datetime) else (high.isoformat() if high else None)
            if not any((low is None or d >= low) and (high is None or d <= high) for d in days):
                return False
        return True


def extension_of(name: str | None) -> str | None:
    if not name:
        return None
    base = name.rsplit("/", 1)[-1]
    if "." not in base.strip("."):
        return None
    ext = base.rsplit(".", 1)[-1].lower()
    return ext if re.fullmatch(r"[a-z0-9]{1,16}", ext) else None
