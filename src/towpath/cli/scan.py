"""``towpath scan``, ``proposals`` and ``item``: towpath-worker's scans and proposals, and decisions about items."""

import json
from pathlib import Path

import typer

from towpath import decisions, scan
from towpath.cli.common import ConfigOpt, _emit, _load

scan_app = typer.Typer(no_args_is_help=True, help="towpath-worker: scans and presence checks.")
proposals_app = typer.Typer(no_args_is_help=True, help="Inspect and export proposals.")
item_app = typer.Typer(no_args_is_help=True, help="Record decisions about items.")


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
