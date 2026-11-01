"""Memories: events gathered into groups the owner reviews (the owner's direction of 2026-10-14).

A group has a kind, a title, a period and member events (claim IDs); an event can belong to many groups.

- **People** (``person``): events whose evidence is mail with one person the owner corresponds with.
- **Life chapters** (``chapter``): the decades of the owner's life, once the profile gives a birthday.
- **Occasions** (``occasion``, ``trip``, ``project``): the model reads a month's events (with a little of the
  months around) and gathers those that belong together, such as a trip's booking, flights and hotel.

Proposals are rebuildable (life store). The owner keeps or dismisses a group, renames it, and adds or removes events;
a kept group is a snapshot in the decisions store, so later proposals never change it. A proposal that mostly
repeats a kept or dismissed group is not shown again.
"""

from towpath.life_groups.common import GROUP_KEY, KINDS, PROFILE_KEY, PROFILE_TARGET
from towpath.life_groups.owner_choices import decide, get, groups, profile, set_profile, undecide
from towpath.life_groups.proposing import propose_groups

__all__ = [
    "GROUP_KEY", "KINDS", "PROFILE_KEY", "PROFILE_TARGET", "decide", "get", "groups", "profile", "propose_groups",
    "set_profile", "undecide",
]
