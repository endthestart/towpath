"""UI launcher. Django is imported only when the optional UI is requested."""

from pathlib import Path

import typer

app = typer.Typer(no_args_is_help=True, help="Local, read-only interface to an existing email index.")


@app.command()
def serve(store_dir: Path = typer.Option(..., "--store-dir", help="Folder containing source.db; no credentials."),
          port: int = typer.Option(8790, min=1024, max=65535)):
    """Serve on this computer at 127.0.0.1. Does not sync, fetch content or call a model."""
    if not (store_dir / "source.db").is_file():
        raise typer.BadParameter("source.db is missing; create the email index first.")
    try:
        from towpath.web.application import launch
    except ModuleNotFoundError as exc:
        if exc.name == "django":
            typer.echo('Install the optional UI first: pip install "towpath[web]"', err=True)
            raise typer.Exit(1) from None
        raise
    typer.echo(f"Towpath is ready at http://127.0.0.1:{port}/ (local, read only)")
    try:
        launch(store_dir.resolve(), port)
    except KeyboardInterrupt:
        typer.echo("Towpath UI stopped.")
