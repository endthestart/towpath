"""``towpath fixtures``: generate and change synthetic fixtures."""

import json
from pathlib import Path

import typer

from towpath.cli.common import _emit
from towpath.fixtures import generator
from towpath.fixtures.domains import check_paths

fixtures_app = typer.Typer(no_args_is_help=True, help="Generate and change synthetic fixtures.")


@fixtures_app.command("generate")
def fixtures_generate(out: Path, seed: int = typer.Option(7, "--seed")):
    """Write synthetic accounts, destination folders, and a config."""
    _emit(generator.generate(out, seed), False)


@fixtures_app.command("advance")
def fixtures_advance(out: Path):
    """Apply the second-run changes: relabels, one deletion, two new messages."""
    summary = generator.advance(out)
    typer.echo(json.dumps({k: summary[k] for k in ("deleted", "added", "relabeled")}))


@fixtures_app.command("expire-cursor")
def fixtures_expire(out: Path):
    """Make stored incremental-sync cursors too old."""
    generator.expire_cursor(out)


@fixtures_app.command("check-domains")
def fixtures_check(paths: list[Path]):
    """Fail if any email address outside reserved domains appears."""
    found = check_paths(paths)
    for path, bad in found.items():
        typer.echo(f"{path}: {', '.join(bad)}")
    raise typer.Exit(1 if found else 0)


@fixtures_app.command("unified")
def fixtures_unified(out: Path, imap_port: int = typer.Option(None, "--imap-port",
                                                              help="Add an IMAP source at 127.0.0.1:PORT "
                                                                   "(run tests/imap_stub.py PORT).")):
    """Generate a synthetic Gmail account, files corpus, NEF inventory manifest, and a config using them."""
    from towpath.unified import environment

    typer.echo(json.dumps(environment.generate(out, imap_port), indent=2))


@fixtures_app.command("files")
def fixtures_files(out: Path):
    """Generate the synthetic file discovery corpus, its fixture catalog, and an example config."""
    from towpath.discovery import corpus

    summary = corpus.generate(out)
    summary["config"] = str(corpus.write_example_config(out))
    _emit(summary, False)
