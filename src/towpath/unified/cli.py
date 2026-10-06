"""``towpath search``: unified, source-aware search (JSON output for people and agents)."""

import json
from pathlib import Path

import typer

from towpath.unified import contracts, federation

app = typer.Typer(no_args_is_help=True,
                  help="Unified search across mail and file sources: sources, search, describe, context.")

MODES = ("ok", "partial", "unavailable")
ConfigOpt = typer.Option(Path("towpath.toml"), "--config", "-c", help="Configuration file.")
LocalOpt = typer.Option(False, "--local", help="Read local catalogs only: no provider, credential or subprocess.")


def _emit(data) -> None:
    typer.echo(json.dumps(data, indent=2, sort_keys=True))


def _fail(code: str, message: str, exit_code: int = 2) -> None:
    _emit({"error": {"code": code, "message": message}})
    raise typer.Exit(exit_code)


@app.command("demo")
def demo(query: str = typer.Argument(..., help="Keyword text and/or filters, e.g. 'extension:nef'."),
         limit: int = typer.Option(10, "--limit", min=1, max=federation.MAX_LIMIT),
         cursor: str = typer.Option(None, "--cursor", help="next_cursor from a previous page."),
         files_mode: str = typer.Option("partial", "--files-mode", help="ok, partial or unavailable."),
         imap_mode: str = typer.Option("ok", "--imap-mode", help="ok, partial or unavailable."),
         gmail_mode: str = typer.Option("ok", "--gmail-mode", help="ok, partial or unavailable.")):
    """Search three synthetic sources (Gmail, IMAP, files). Touches no account, file or store."""
    from towpath.unified import fixtures

    for mode in (files_mode, imap_mode, gmail_mode):
        if mode not in MODES:
            _fail("invalid-request", f"mode must be one of {', '.join(MODES)}")
    try:
        filters = contracts.Filters.parse(query)
        adapters = fixtures.demo_adapters(files_mode, imap_mode, gmail_mode)
        result = federation.search(adapters, filters, limit, cursor).to_dict()
    except contracts.ContractError as exc:
        _fail("invalid-request", str(exc))
    result["synthetic"] = True
    _emit(result)


def _adapters(config: Path, local: bool):
    from towpath import config as config_mod
    from towpath.unified import sources

    try:
        cfg = config_mod.load(config)
    except (config_mod.ConfigError, OSError) as exc:
        _fail("invalid-config", str(exc))
    return sources.build_adapters(cfg, connect=not local)


@app.command("sources")
def sources_cmd(config: Path = ConfigOpt, local: bool = LocalOpt):
    """The source registry: each source's state, capabilities, search depths, scope, coverage and freshness."""
    adapters = _adapters(config, local)
    try:
        _emit({"sources": federation.statuses(adapters)})
    finally:
        federation.close_all(adapters)


@app.command("query")
def query_cmd(query: str = typer.Argument(..., help="Keyword text and/or filters, e.g. 'canal extension:pdf'."),
              source: list[str] = typer.Option(None, "--source", help="Limit to these source IDs (repeatable)."),
              limit: int = typer.Option(20, "--limit", min=1, max=federation.MAX_LIMIT, help="Results per source."),
              cursor: str = typer.Option(None, "--cursor", help="next_cursor from a previous page."),
              config: Path = ConfigOpt, local: bool = LocalOpt):
    """One page from each source. Without --local, keyword text also asks the providers themselves
    (Gmail search, IMAP SEARCH, the files provider's index) under their existing read-only access and pacing."""
    adapters = _adapters(config, local)
    try:
        filters = contracts.Filters.parse(query, sources=source or None)
        _emit(federation.search(adapters, filters, limit, cursor).to_dict())
    except contracts.ContractError as exc:
        _fail("invalid-request", str(exc))
    finally:
        federation.close_all(adapters)


@app.command("describe")
def describe_cmd(ref: str = typer.Argument(..., help="A result reference, <source>:<native>."),
                 config: Path = ConfigOpt, local: bool = LocalOpt):
    """Inspect one reference: its record, parts or locator, and its state as its source reports it."""
    adapters = _adapters(config, local)
    try:
        _emit(federation.describe(adapters, ref))
    except (contracts.ContractError, federation.UnknownReference) as exc:
        _fail("not-found" if isinstance(exc, federation.UnknownReference) else "invalid-request", str(exc), 1)
    except Exception as exc:  # noqa: BLE001 - reported as data like any source error
        page = federation.error_page(contracts.Reference.parse(ref).source_id, exc)
        _fail(page.error.code, page.error.message, 1)
    finally:
        federation.close_all(adapters)
