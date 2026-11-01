"""The owner's mail rules: what to look for, and what to do about it.

A rule has static conditions (sender, recipient, subject and text patterns, categories, mailbox) and, optionally,
a condition in plain words that the model checks ("asks me to pay something"). Its actions are mailbox changes from
``towpath.mail.actions``. Rules live in ``maildecisions``; every change adds a revision, and a change to what the
rule looks for or does also a new version. Proposals name the version that made them (``rule:<id>@<version>``), so
switching a rule on, off or to automatic doesn't make it look at mail again.

The worker looks at each message once per rule version: new mail only, or the whole history when the owner asks for
that. Static conditions decide alone when there is no plain-words condition. Messages that pass the static
conditions of rules with plain-words conditions go to the model together, which picks the one rule that fits (or
none). A match proposes the rule's actions. A rule in ``propose`` mode waits for the owner's approval; one the owner
switched to ``auto`` is carried out without asking, but only for reversible kinds of change, and still only as dry
runs while the account is in dry runs only.

Patterns: text without ``*`` or ``?`` matches anywhere, ignoring case; with them it is a wildcard pattern over the
whole value (``*@shop.example``).
"""

from towpath.mail.rules.common import FIELDS, MODES, TEMPLATES, RuleError
from towpath.mail.rules.matching import evaluate_pending, history, preview, static_match
from towpath.mail.rules.owner_rules import (
    add_inbox_zero,
    all_rules,
    auto_approved,
    change,
    get,
    producer,
    save,
    validate,
)

__all__ = [
    "FIELDS", "MODES", "RuleError", "TEMPLATES", "add_inbox_zero", "all_rules", "auto_approved", "change",
    "evaluate_pending", "get", "history", "preview", "producer", "save", "static_match", "validate",
]
