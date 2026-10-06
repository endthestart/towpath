"""``towpath search``: unified, source-aware search (JSON output for people and agents)."""

import json

import typer

from towpath.unified import contracts, federation

app = typer.Typer(no_args_is_help=True,
                  help="Unified search across mail and file sources: sources, search, describe, context.")

MODES = ("ok", "partial", "unavailable")


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
