"""The adapters for a configuration, or for whatever the stores hold."""

import json

from towpath.stores import open_store
from towpath.unified.federation import SourceAdapter
from towpath.unified.sources.common import READ_ROLE
from towpath.unified.sources.files import FilesAdapter
from towpath.unified.sources.mail import MailAdapter


def build_adapters(config, connect: bool = False) -> dict[str, SourceAdapter]:
    """Every configured mail source and files provider, in configuration order."""
    adapters: dict[str, SourceAdapter] = {}
    for source in config.sources.values():
        if source.kind == "mail-provider":
            adapters[source.id] = MailAdapter(config, source, connect)
    if config.files is not None:
        for provider_config in config.files.providers.values():
            adapter = FilesAdapter(config, provider_config, connect)
            if adapter.source_id in adapters:
                raise ValueError(f"source id {adapter.source_id} is used by both a mail source and a files provider")
            adapters[adapter.source_id] = adapter
    return adapters


def store_adapters(store_dir) -> dict[str, SourceAdapter]:
    """Mail sources known to the source store, for a UI started without a configuration (local mode only)."""
    from types import SimpleNamespace

    config = SimpleNamespace(store_dir=store_dir, files=None, sources={})
    adapters: dict[str, SourceAdapter] = {}
    db = open_store(store_dir, "source", READ_ROLE)
    try:
        rows = db.execute("SELECT source_id, adapter, descriptor FROM sources WHERE kind = 'mail-provider' "
                          "ORDER BY source_id").fetchall()
    finally:
        db.close()
    for row in rows:
        try:
            descriptor = json.loads(row["descriptor"] or "{}")
        except ValueError:
            descriptor = {}
        boxes = descriptor.get("mailboxes")
        source = SimpleNamespace(id=row["source_id"], adapter=row["adapter"], kind="mail-provider",
                                 mailboxes=tuple(boxes) if isinstance(boxes, list) else None)
        try:
            adapters[row["source_id"]] = MailAdapter(config, source, connect=False)
        except Exception:  # noqa: BLE001 - a malformed source id is skipped, not fatal
            continue
    return adapters
