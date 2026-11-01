"""What the command groups share: common options, loading the configuration, and printing results."""

import json
from pathlib import Path

import typer

from towpath import config as config_mod

ConfigOpt = typer.Option(Path("towpath.toml"), "--config", "-c", help="Configuration file.")


def _load(path: Path):
    try:
        return config_mod.load(path)
    except config_mod.ConfigError as exc:
        raise typer.BadParameter(str(exc)) from exc


CredentialsDirOpt = typer.Option(None, "--credentials-dir", envvar="TOWPATH_CREDENTIALS_DIR",
                                 help="The connector's private folder for passwords added in the UI.")
DataDirOpt = typer.Option(None, "--data-dir", envvar="TOWPATH_DATA_DIR",
                          help="The instance's data folder; its layout is created on first start (see install guide).")
OptionalConfigOpt = typer.Option(None, "--config", "-c", help="Configuration file (optional with --data-dir).")


def _emit(data, as_json: bool) -> None:
    if as_json:
        typer.echo(json.dumps(data, indent=2, sort_keys=True))
    elif isinstance(data, list):
        for row in data:
            typer.echo(json.dumps(row, sort_keys=True))
    else:
        for key, value in data.items():
            typer.echo(f"{key}: {value}")
