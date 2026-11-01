"""Reading a message's MIME structure from what the server returned."""

from email.header import decode_header, make_header
from email.utils import collapse_rfc2231_value

from towpath.adapters.imap.session import _text


def _params(raw) -> dict:
    if not raw or not isinstance(raw, (tuple, list)):
        return {}
    items = [_text(x) for x in raw]
    return {items[i].lower(): items[i + 1] for i in range(0, len(items) - 1, 2)}


def _decoded(value: str | None) -> str | None:
    if not value:
        return value
    try:
        return str(make_header(decode_header(collapse_rfc2231_value(value))))
    except (LookupError, ValueError):
        return value


def _leaf(node, part_id: str, depth: int) -> dict:
    mtype, subtype = (_text(node[0]) or "").lower(), (_text(node[1]) or "").lower()
    params = _params(node[2])
    # Extension data positions differ by type (RFC 9051 section 7.5.2).
    dsp_index = 9 if mtype == "text" else (11 if (mtype, subtype) == ("message", "rfc822") else 8)
    disposition, dsp_params = None, {}
    if len(node) > dsp_index and isinstance(node[dsp_index], (tuple, list)) and node[dsp_index]:
        disposition = (_text(node[dsp_index][0]) or "").lower() or None
        dsp_params = _params(node[dsp_index][1] if len(node[dsp_index]) > 1 else None)
    filename = dsp_params.get("filename") or params.get("name")
    return {"part_id": part_id, "depth": depth, "mime_type": f"{mtype}/{subtype}", "charset": params.get("charset"),
            "filename": _decoded(filename), "disposition": disposition, "size": int(node[6] or 0),
            "attachment_id": None, "encoding": (_text(node[5]) or "7bit").lower()}


def flatten_structure(body, prefix: str = "", depth: int = 0) -> list[dict]:
    """Leaf parts with IMAP part numbers (1, 1.2, ...). A single-part message's body is part 1."""
    if body is None:
        return []
    if isinstance(body[0], list):
        parts = []
        for i, child in enumerate(body[0], start=1):
            parts.extend(flatten_structure(child, f"{prefix}{i}.", depth + 1) if isinstance(child[0], list)
                         else [_leaf(child, f"{prefix}{i}", depth + 1)])
        return parts
    return [_leaf(body, prefix.rstrip(".") or "1", depth)]
