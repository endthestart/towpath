"""Whether each kind of saved decision may be restored."""

import json
from datetime import date

from towpath import decisions as item_settings, life, life_conflicts, life_groups, life_story, space


def _str(*values) -> bool:
    return all(isinstance(v, str) and v for v in values)


def _review(value) -> bool:
    if not isinstance(value, dict) or value.get("state") not in ("accepted", "rejected", "corrected", "proposed"):
        return False
    if not _str(value.get("by"), value.get("at")):
        return False
    correction = value.get("correction")
    if correction is None:
        return value["state"] != "corrected"
    if not isinstance(correction, dict) or not correction or set(correction) - {"statement", "when"}:
        return False
    if "statement" in correction and not _str(correction["statement"]):
        return False
    if "when" in correction:
        try:
            life.When.from_dict(correction["when"]).bounds()
        except Exception:  # noqa: BLE001 - any malformed date is refused
            return False
    return True


def _group(value) -> bool:
    return (isinstance(value, dict) and value.get("state") in ("kept", "dismissed") and
            _str(value.get("kind"), value.get("title"), value.get("by"), value.get("at")) and
            isinstance(value.get("members"), list) and all(_str(m) for m in value["members"]) and
            all(_str(m) for m in value.get("proposed_members") or []) and
            isinstance(value.get("proposed_members", []), list))


def _profile(value) -> bool:
    if not isinstance(value, dict) or set(value) - {"name", "birthday"}:
        return False
    if value.get("name") is not None and not (isinstance(value["name"], str) and len(value["name"]) <= 80):
        return False
    try:
        return value.get("birthday") is None or bool(date.fromisoformat(value["birthday"]))
    except (TypeError, ValueError):
        return False


def _loads(value: str):
    try:
        return json.loads(value)
    except (ValueError, RecursionError):
        return None


def restorable_setting(target: str, key: str, value: str) -> bool:
    """Whether a decisions-store setting is one of the owner's decisions a file may bring back, and is well formed."""
    if target.startswith(("claim:", "group:")) and key == life_story.SHARE_KEY:
        return value == life_story.SHAREABLE  # marked for the family edition
    if target.startswith("claim:"):
        if key == life.REVIEW_KEY:
            return _review(_loads(value))
        if key == life.RECOLLECTION_KEY:
            try:
                life.Claim.from_dict(json.loads(value))
                return True
            except (life.ClaimError, ValueError, KeyError, TypeError):
                return False
        return False
    if target.startswith("group:"):
        return key == life_groups.GROUP_KEY and _group(_loads(value))
    if (target, key) == (life_groups.PROFILE_TARGET, life_groups.PROFILE_KEY):
        return _profile(_loads(value))
    if target == life_conflicts.TARGET:
        return _loads(value) == "both-right"
    if target.startswith("address:"):  # the owner joined this address to a person
        found = _loads(value)
        address = target.split(":", 1)[1]
        return (key == "person" and isinstance(found, str) and "@" in found and "@" in address and
                found == found.lower() and address == address.lower() and found != address)
    if target.startswith("space:"):
        return key == space.KEEP_KEY and value == space.KEEP_VALUE
    if key in item_settings.SETTINGS:
        return value in item_settings.SETTINGS[key]
    return False


def draft_notes(value: str) -> bool:
    from towpath.mail.threads import NOTES_MAX

    found = _loads(value)
    return isinstance(found, str) and len(found) <= NOTES_MAX


def collection_ok(kind, definition) -> bool:
    """A collection definition the collection pages can read."""
    from towpath.unified import collections
    from towpath.unified.contracts import Filters

    if kind not in collections.KINDS:
        return False
    if kind == "set":
        return definition is None
    if not isinstance(definition, str):
        return False
    try:
        found = json.loads(definition)
        if kind == "photos":
            return isinstance(found, dict) and collections.photo_filters(found) == found
        Filters.from_dict(found)
        return True
    except Exception:  # noqa: BLE001 - anything unreadable is refused
        return False
