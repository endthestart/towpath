"""UI launcher. Django is imported only when the optional UI is requested."""

from pathlib import Path

import typer

app = typer.Typer(no_args_is_help=True, help="Read-only interface to existing indexes, behind a single-account login.")


@app.command()
def serve(store_dir: Path = typer.Option(None, "--store-dir", help="Folder holding the stores; no credentials."),
          data_dir: Path = typer.Option(None, "--data-dir", envvar="TOWPATH_DATA_DIR",
                                        help="The instance's data folder (the web service sees only its state/)."),
          port: int = typer.Option(8790, min=1024, max=65535),
          host: str = typer.Option("127.0.0.1", "--host",
                                   help="Listening address. Anything but loopback belongs behind a TLS reverse proxy."),
          public_url: str = typer.Option(None, "--public-url",
                                         help="Address the reverse proxy serves, e.g. https://towpath.example.org."),
          config: Path = typer.Option(None, "--config", "-c",
                                      help="Optional configuration: adds file sources and their declared scope. "
                                           "Credentials in it are never read.")):
    """Serve the UI (127.0.0.1 unless --host). Does not sync, fetch content or call a model.

    Every page requires the instance's one account. On first run the server prints a setup code to its log;
    open /setup and enter it to create the account."""
    from towpath import config as config_mod

    layout = None
    if data_dir is not None:
        from towpath import layout as layout_mod

        try:
            layout = layout_mod.state_only(data_dir)
        except layout_mod.LayoutError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(2) from None
        store_dir = layout.state
    elif store_dir is None:
        raise typer.BadParameter("set --data-dir, or --store-dir to the folder holding the stores")
    if not store_dir.is_dir():
        raise typer.BadParameter(f"{store_dir} is not a folder")
    from towpath import stores

    try:  # the UI never migrates a store; it refuses to start until an explicit upgrade has run
        stores.require_current(store_dir, None)
    except stores.SchemaOutdated as exc:
        raise typer.BadParameter(str(exc)) from None
    loaded = None
    try:
        if config is not None:
            loaded = config_mod.load(config, store_dir=store_dir if layout is not None else None)
        elif layout is not None:  # no settings file is visible here; accounts come from the Connections page
            loaded = config_mod.for_data(layout)
    except (config_mod.ConfigError, OSError) as exc:
        raise typer.BadParameter(str(exc)) from None
    try:
        from towpath.web.application import launch
    except ModuleNotFoundError as exc:
        if exc.name == "django":
            typer.echo('Install the optional UI first: pip install "towpath[web]"', err=True)
            raise typer.Exit(1) from None
        raise
    if public_url is not None and not public_url.startswith(("https://", "http://")):
        raise typer.BadParameter("--public-url must start with https:// or http://")
    typer.echo(f"Towpath is ready at {public_url or f'http://{host}:{port}/'} (sources read only)")
    try:
        launch(store_dir.resolve(), port, loaded, host, public_url)
    except KeyboardInterrupt:
        typer.echo("Towpath UI stopped.")


@app.command("reset-login")
def reset_login(store_dir: Path = typer.Option(..., "--store-dir", help="Folder the UI serves."),
                yes: bool = typer.Option(False, "--yes", help="Do not ask for confirmation.")):
    """Remove the UI account and sign everyone out. The next visit starts first-run setup with a new code."""
    from towpath.web import auth

    if not yes:
        typer.confirm("Remove the Towpath UI account?", abort=True)
    removed = auth.reset_account(store_dir)
    typer.echo("Account removed; open /setup and use the new code from the server log." if removed
               else "No account was set up.")
