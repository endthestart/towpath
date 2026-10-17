"""Keeping mail from cloud models: the owner marks a sender, or one message, "keep from cloud models".

The mark is an item setting in the decisions store (``model_use = no-cloud``) on ``sender:<account>:<address>`` or on
the message's item ID. Every model call about mail names both (``targets``), so the gateway refuses to send such mail
to an endpoint at a cloud service, while models on the owner's network still read it.
"""

from contextlib import closing

from towpath.stores import open_store, store_path


def sender_target(source_id: str, address: str) -> str:
    return f"sender:{source_id}:{(address or '').lower()}"


def targets(source_id: str, item_ids: list[str], addresses) -> list[str]:
    """What a model call about these messages names, for the gateway's per-item checks."""
    return list(item_ids) + sorted({sender_target(source_id, a) for a in addresses if a})


def kept(store_dir, ids: list[str], role: str = "web") -> set[str]:
    """Which of these targets are kept from cloud models."""
    if not ids or not store_path(store_dir, "decisions").exists():
        return set()
    with closing(open_store(store_dir, "decisions", role)) as db:
        marks = ",".join("?" for _ in ids)
        return {r[0] for r in db.execute(f"SELECT target_id FROM settings WHERE key = 'model_use' AND value = "
                                         f"'no-cloud' AND target_id IN ({marks})", ids)}


def keep(config, target: str, on: bool, author: str = "owner") -> None:
    from towpath import decisions

    decisions.set_item(config, target, author=author, model_use="no-cloud" if on else "follow-grants")


def ref_targets(store_dir, refs, role: str = "worker") -> dict[str, list[str]]:
    """For mail references (``<source>:<native id>``, as life events cite them): each one's message and sender as
    gateway targets. References that aren't mail Towpath has indexed map to nothing."""
    out: dict[str, list[str]] = {}
    if not store_path(store_dir, "source").exists():
        return out
    with closing(open_store(store_dir, "source", role)) as src:
        for ref in set(refs):
            sid, _, native = str(ref).partition(":")
            row = src.execute("SELECT item_id, from_addr FROM items WHERE source_id = ? AND native_id = ?",
                              (sid, native.split("#part=")[0])).fetchone()
            if row is not None:
                out[ref] = targets(sid, [row[0]], [row[1]])
    return out
