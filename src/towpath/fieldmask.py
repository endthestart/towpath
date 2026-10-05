"""Google-style partial-response field masks.

Gmail's ``format=full`` response carries parsed body content, and a MIME part
can hold its bytes inline (``body.data``) instead of an attachment ID. The
connector therefore asks only for structural fields at every nesting level
(decision D16). This module builds that mask, parses mask strings, and applies
them, so the fixture adapter can behave like the real API.
"""

STRUCTURE_FIELDS = "partId,mimeType,filename,headers,body(size,attachmentId)"
MESSAGE_FIELDS = "id,threadId,labelIds,historyId,internalDate,sizeEstimate"
DEFAULT_DEPTH = 8


def part_mask(depth: int) -> str:
    """Structural fields for a part and its children, ``depth`` levels down.

    The deepest level asks only for ``parts(partId)``: if that sentinel comes
    back, the message is nested deeper than the mask covers.
    """
    if depth == 0:
        return f"{STRUCTURE_FIELDS},parts(partId)"
    return f"{STRUCTURE_FIELDS},parts({part_mask(depth - 1)})"


def message_mask(depth: int = DEFAULT_DEPTH) -> str:
    return f"{MESSAGE_FIELDS},payload({part_mask(depth)})"


def parse(mask: str) -> dict:
    """Parse ``a,b(c,d)`` into ``{"a": None, "b": {"c": None, "d": None}}``."""
    pos = 0

    def parse_list() -> dict:
        nonlocal pos
        result: dict = {}
        name = ""
        while pos < len(mask):
            ch = mask[pos]
            if ch == ",":
                if name:
                    result[name] = None
                name = ""
                pos += 1
            elif ch == "(":
                pos += 1
                result[name] = parse_list()
                name = ""
            elif ch == ")":
                pos += 1
                if name:
                    result[name] = None
                return result
            else:
                name += ch
                pos += 1
        if name:
            result[name] = None
        return result

    return parse_list()


def apply(value, mask: dict | None):
    """Keep only the fields named in ``mask``; ``None`` keeps everything."""
    if mask is None:
        return value
    if isinstance(value, list):
        return [apply(item, mask) for item in value]
    if isinstance(value, dict):
        return {key: apply(value[key], sub) for key, sub in mask.items() if key in value}
    return value
