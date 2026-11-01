"""``towpath collections``: durable saved queries and reference sets."""

from pathlib import Path

import typer

from towpath.unified import contracts, federation
from towpath.unified.cli.search_commands import ConfigOpt, LocalOpt, _adapters, _config, _emit, _fail

collections_app = typer.Typer(no_args_is_help=True,
                              help="Durable collections: saved queries and reference sets (JSON output).")


def _collections_call(fn):
    from towpath.unified import collections

    try:
        _emit(fn(collections))
    except (collections.CollectionError, contracts.ContractError) as exc:
        _fail("invalid-request", str(exc))


@collections_app.command("list")
def collections_list(config: Path = ConfigOpt):
    """Every collection with its kind and size."""
    cfg = _config(config)
    _collections_call(lambda c: {"collections": c.list_all(cfg.store_dir)})


@collections_app.command("show")
def collections_show(collection: str, config: Path = ConfigOpt):
    """One collection's definition, members and accepted baseline (for a photo collection, only how many: a photo
    accepted then may be hidden since)."""
    cfg = _config(config)

    def show(c):
        found = c.get(cfg.store_dir, collection)
        if found["kind"] == "photos":
            found["baseline"] = {"accepted": len(found["baseline"])}
        return found
    _collections_call(show)


@collections_app.command("create")
def collections_create(name: str, query: str = typer.Option(None, "--query", help="Save this query; omit for a set."),
                       source: list[str] = typer.Option(None, "--source"), config: Path = ConfigOpt):
    """Create a reference set, or a query collection with --query (web role)."""
    cfg = _config(config)

    def make(c):
        if query:
            return c.create(cfg.store_dir, name, "query", contracts.Filters.parse(query, sources=source or None))
        return c.create(cfg.store_dir, name, "set")
    _collections_call(make)


@collections_app.command("add")
def collections_add(collection: str, ref: str, note: str = typer.Option(None, "--note"), config: Path = ConfigOpt,
                    local: bool = LocalOpt):
    """Add a reference to a set, recording the version its source reports now (web role)."""
    cfg = _config(config)
    adapters = _adapters(config, local)
    try:
        described = federation.describe(adapters, ref)
    except (federation.UnknownReference, contracts.ContractError) as exc:
        _fail("not-found", str(exc), 1)
    finally:
        federation.close_all(adapters)
    result = described["result"]
    _collections_call(lambda c: c.add(cfg.store_dir, collection, ref, result.get("version"), result.get("title"),
                                      result.get("source_type"), note))


@collections_app.command("remove")
def collections_remove(collection: str, ref: str, config: Path = ConfigOpt):
    """Remove a reference from a set (web role)."""
    cfg = _config(config)
    _collections_call(lambda c: {"removed": c.remove(cfg.store_dir, collection, ref)})


@collections_app.command("evaluate")
def collections_evaluate(collection: str, accept: bool = typer.Option(False, "--accept",
                                                                      help="Make the results the new baseline."),
                         config: Path = ConfigOpt, local: bool = LocalOpt):
    """Re-check a collection now: unchanged, changed, unverified, added, removed, unavailable, and partiality."""
    cfg = _config(config)
    adapters = _adapters(config, local)

    def run(c):
        evaluation = c.evaluate(adapters, c.get(cfg.store_dir, collection), "local" if local else "connect",
                                store_dir=cfg.store_dir)
        if accept:
            evaluation["accepted"] = c.accept(cfg.store_dir, collection, evaluation)
        return evaluation
    try:
        _collections_call(run)
    finally:
        federation.close_all(adapters)
