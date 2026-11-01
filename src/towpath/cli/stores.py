"""``towpath stores``: check and upgrade existing stores."""

import json
from pathlib import Path

import typer

stores_app = typer.Typer(no_args_is_help=True,
                         help="Check and upgrade existing stores (each opened only by its own writer role).")


StoreDirOpt = typer.Option(..., "--store-dir", help="The folder holding source.db and the other stores.")
StoreOpt = typer.Option(None, "--store", help="Limit to these stores (repeatable); default: every existing store.")


def _store_names(names):
    from towpath.stores import SCHEMAS

    unknown = [n for n in names or () if n not in SCHEMAS]
    if unknown:
        raise typer.BadParameter(f"unknown store(s) {', '.join(unknown)}; expected {', '.join(SCHEMAS)}")
    return list(names) if names else None


@stores_app.command("status")
def stores_status(store_dir: Path = StoreDirOpt, store: list[str] = StoreOpt):
    """Read-only: which tables or columns each existing store lacks. Exit 1 if any store needs an upgrade."""
    from towpath import stores

    reports = stores.schema_status(store_dir, _store_names(store))
    current = all(r["current"] for r in reports)
    typer.echo(json.dumps({"current": current, "stores": reports}, indent=2, sort_keys=True))
    raise typer.Exit(0 if current else 1)


@stores_app.command("upgrade")
def stores_upgrade(store_dir: Path = StoreDirOpt, store: list[str] = StoreOpt):
    """Add new tables and columns to existing stores. Idempotent; never changes or deletes existing rows.

    Stop other Towpath commands and the UI first, and keep a copy of the folder. No rescan is needed."""
    from towpath import stores

    if not store_dir.is_dir():
        raise typer.BadParameter(f"{store_dir} is not a folder")
    reports = stores.upgrade(store_dir, _store_names(store))
    typer.echo(json.dumps({"current": all(r["current"] for r in reports), "stores": reports}, indent=2,
                          sort_keys=True))
