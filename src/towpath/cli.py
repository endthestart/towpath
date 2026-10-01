"""Command line for the first slice.

Command groups map to roles: ``connect`` is towpath-connect, ``scan`` and
``proposals`` are towpath-worker, and ``item`` and ``scan dismiss`` are the
web role's decisions. Each command runs in its own process.
"""

import json
from pathlib import Path

import typer

from towpath import config as config_mod
from towpath import connect, decisions, scan
from towpath.fixtures import generator
from towpath.fixtures.domains import check_paths

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Towpath first slice (synthetic, read-only).")
connect_app = typer.Typer(no_args_is_help=True, help="towpath-connect: read sources, fetch requested content.")
scan_app = typer.Typer(no_args_is_help=True, help="towpath-worker: scans and presence checks.")
proposals_app = typer.Typer(no_args_is_help=True, help="Inspect and export proposals.")
item_app = typer.Typer(no_args_is_help=True, help="Record decisions about items.")
fixtures_app = typer.Typer(no_args_is_help=True, help="Generate and change synthetic fixtures.")
app.add_typer(connect_app, name="connect")
app.add_typer(scan_app, name="scan")
app.add_typer(proposals_app, name="proposals")
app.add_typer(item_app, name="item")
app.add_typer(fixtures_app, name="fixtures")

ConfigOpt = typer.Option(Path("towpath.toml"), "--config", "-c", help="Configuration file.")


def _load(path: Path):
    try:
        return config_mod.load(path)
    except config_mod.ConfigError as exc:
        raise typer.BadParameter(str(exc)) from exc


def _emit(data, as_json: bool) -> None:
    if as_json:
        typer.echo(json.dumps(data, indent=2, sort_keys=True))
    elif isinstance(data, list):
        for row in data:
            typer.echo(json.dumps(row, sort_keys=True))
    else:
        for key, value in data.items():
            typer.echo(f"{key}: {value}")


@connect_app.command("sync")
def connect_sync(source: list[str] = typer.Argument(None, help="Source IDs; all sources if omitted."),
                 config: Path = ConfigOpt, as_json: bool = typer.Option(False, "--json"),
                 simulate_interrupt_after: int = typer.Option(None, hidden=True),
                 simulate_ignored_mask: bool = typer.Option(False, hidden=True)):
    """Index sources: full sync first, incremental after."""
    cfg = _load(config)
    results = []
    for source_id in source or list(cfg.sources):
        options = {}
        if cfg.sources[source_id].adapter == "fixture-gmail":
            options = {"interrupt_after": simulate_interrupt_after, "ignore_mask": simulate_ignored_mask}
        results.append(connect.sync(cfg, source_id, **options))
    _emit(results, as_json)


@connect_app.command("fetch-requests")
def connect_fetch(config: Path = ConfigOpt, as_json: bool = typer.Option(False, "--json")):
    """Fetch the parts that scans asked for, one at a time."""
    _emit(connect.fetch_requests(_load(config)), as_json)


@connect_app.command("coverage")
def connect_coverage(source: str, config: Path = ConfigOpt, as_json: bool = typer.Option(False, "--json")):
    """Show each run's completeness and counts for a source."""
    _emit(connect.coverage(_load(config), source), as_json)


@scan_app.command("run")
def scan_run(selector: list[str] = typer.Argument(None, help="Selector IDs; all if omitted."),
             config: Path = ConfigOpt, as_json: bool = typer.Option(False, "--json")):
    """Run selectors, request missing content, and propose deliveries for absent files."""
    cfg = _load(config)
    _emit([scan.run_scan(cfg, s) for s in (selector or list(cfg.selectors))], as_json)


@scan_app.command("dismiss")
def scan_dismiss(proposal_id: str, reason: str = typer.Option("", "--reason"), config: Path = ConfigOpt):
    """Dismiss a proposal; the same match is never proposed again."""
    key = decisions.dismiss(_load(config), proposal_id, reason)
    typer.echo(f"dismissed {proposal_id} ({key})")


@scan_app.command("model-queue")
def scan_model_queue(config: Path = ConfigOpt, as_json: bool = typer.Option(False, "--json")):
    """Items waiting for a model classifier (no endpoint exists in this slice)."""
    _emit(scan.model_queue(_load(config)), as_json)


@proposals_app.command("list")
def proposals_list(state: str = typer.Option(None, "--state"), config: Path = ConfigOpt,
                   as_json: bool = typer.Option(False, "--json")):
    rows = scan.list_proposals(_load(config), state)
    if as_json:
        _emit(rows, True)
        return
    for r in rows:
        b = r["body"]
        typer.echo(f"{r['proposal_id']}  {r['state']:<16} {b['file_name']} -> {b['destination_id']}")


@proposals_app.command("export")
def proposals_export(out: Path = typer.Option(Path("-"), "--out"), config: Path = ConfigOpt):
    """Export proposals as JSON Lines. Nothing in an export can execute them."""
    lines = [json.dumps({"proposal_id": r["proposal_id"], "state": r["state"], "digest": r["digest"], **r["body"]},
                        sort_keys=True) for r in scan.list_proposals(_load(config))]
    text = "\n".join(lines) + ("\n" if lines else "")
    if str(out) == "-":
        typer.echo(text, nl=False)
    else:
        out.write_text(text)


@item_app.command("set")
def item_set(item_id: str, audience: str = typer.Option(None, "--audience"),
             model_use: str = typer.Option(None, "--model-use"), config: Path = ConfigOpt):
    """Set an item's audience and model use."""
    try:
        changed = decisions.set_item(_load(config), item_id, audience=audience, model_use=model_use)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(json.dumps({"item_id": item_id, **changed}, sort_keys=True))


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
