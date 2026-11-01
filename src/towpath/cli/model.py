"""``towpath model``: probe and grant model endpoints, and run queued work."""

from pathlib import Path

import typer

from towpath import config as config_mod
from towpath import decisions
from towpath.cli.common import ConfigOpt, DataDirOpt, _emit, _load

model_app = typer.Typer(no_args_is_help=True, help="Model endpoints: probe, grant, and run queued work.")


@model_app.command("probe")
def model_probe(endpoint: str, config: Path = ConfigOpt, as_json: bool = typer.Option(False, "--json")):
    """Probe an endpoint with synthetic prompts and record its capabilities (worker)."""
    from towpath.models import gateway
    from towpath.models.profiles import ProfileError

    try:
        report = gateway.probe(_load(config), endpoint)
    except ProfileError as exc:
        raise typer.BadParameter(str(exc)) from exc
    _emit(report, as_json)


def _decision_config(config: Path, data_dir: Path | None, role: str = "web"):
    """For web-role decisions: the configuration file, or a data folder's configuration with its connections."""
    if data_dir is None:
        return _load(config)
    from towpath import connections, layout as layout_mod

    layout = layout_mod.Layout(Path(data_dir))
    return connections.merged(config_mod.for_data(layout), None, role=role)


@model_app.command("grant")
def model_grant(endpoint: str, data_class: str, config: Path = ConfigOpt, data_dir: Path = DataDirOpt,
                author: str = typer.Option("local-user", "--author", help="Who gave the grant, for the log.")):
    """Allow a data class on a named endpoint's current profile (web role decision)."""
    try:
        fp = decisions.grant_model(_decision_config(config, data_dir), endpoint, data_class, author=author)
    except (ValueError, KeyError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"granted {data_class} on {endpoint} (profile {fp}); changing the profile voids this grant")


@model_app.command("use")
def model_use(task: str, endpoint: str = typer.Argument(None, help="Endpoint ID; leave out for the default"),
              config: Path = ConfigOpt, data_dir: Path = DataDirOpt,
              author: str = typer.Option("local-user", "--author")):
    """Choose the model endpoint one task uses (web role decision)."""
    from towpath import connections

    cfg = _decision_config(config, data_dir)
    if task not in connections.MODEL_TASKS:
        raise typer.BadParameter(f"task must be one of {', '.join(connections.MODEL_TASKS)}")
    if endpoint and endpoint not in cfg.endpoints:
        raise typer.BadParameter(f"no endpoint {endpoint!r}; known: {', '.join(sorted(cfg.endpoints))}")
    decisions.set_task_model(cfg.store_dir, task, endpoint, author=author)
    typer.echo(f"{task}: {endpoint or 'default'}")


@model_app.command("run-queue")
def model_run_queue(config: Path = ConfigOpt, as_json: bool = typer.Option(False, "--json")):
    """Process queued model work through the gateway (worker)."""
    from towpath.models import queue

    _emit(queue.run_queue(_load(config)), as_json)


@model_app.command("check")
def model_check(task: str, data_class: str = typer.Option("metadata", "--data-class"), config: Path = ConfigOpt):
    """Show whether the gateway would allow a task, without calling any model."""
    from towpath.models import gateway

    status, reason, endpoint = gateway.check_policy(_load(config), task, data_class, [])
    typer.echo(f"{status}: {reason or 'allowed'} (endpoint {getattr(endpoint, 'id', None)})")
