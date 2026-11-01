"""``towpath connect``: towpath-connect reads sources and fetches requested content."""

from pathlib import Path

import typer

from towpath import config as config_mod
from towpath import connect
from towpath.cli.common import ConfigOpt, CredentialsDirOpt, DataDirOpt, OptionalConfigOpt, _emit, _load

connect_app = typer.Typer(no_args_is_help=True, help="towpath-connect: read sources, fetch requested content.")


def _check_scratch(scratch: Path) -> None:
    """SQLite writes the temporary files of large sorts to ``SQLITE_TMPDIR``, which the connector image sets to the
    data folder's scratch/. If that isn't the folder in use, or can't be written, SQLite falls back to /tmp, which a
    container keeps small, and large sorts fail later: warn now. Unset (outside the image) means the system default."""
    import os

    configured = os.environ.get("SQLITE_TMPDIR")
    if configured is None or (Path(configured).resolve() == scratch.resolve()
                              and os.access(scratch, os.W_OK | os.X_OK)):
        return
    typer.echo(f"warning: SQLite's temporary files will go to /tmp, not {scratch}; large sorts may fail "
               f"(set SQLITE_TMPDIR={scratch} and make it writable)", err=True)


def _connector_setup(data_dir: Path | None, config: Path | None, credentials_dir: Path | None):
    """The configuration and credentials folder for a connector command, from a data folder or explicit paths."""
    if data_dir is not None:
        from towpath import layout as layout_mod, stores

        try:
            layout = layout_mod.prepare(data_dir)
            cfg = config_mod.for_data(layout, config)
        except layout_mod.LayoutError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(2) from None
        except config_mod.ConfigError as exc:
            raise typer.BadParameter(str(exc)) from exc
        _check_scratch(layout.data / "scratch")
        # The stores the connector process writes (as connect, worker or act) gain this version's tables and indexes
        # before the web service, which starts after it and checks for them. Stores that don't exist yet are left
        # to their writer.
        for name, roles in stores.WRITERS.items():
            role = next((r for r in ("connect", "worker", "act") if r in roles), None)
            if role and stores.store_path(cfg.store_dir, name).exists():
                stores.open_store(cfg.store_dir, name, role).close()
        return cfg, credentials_dir or layout.credentials
    return _load(config or Path("towpath.toml")), credentials_dir


@connect_app.command("run-worker")
def request_worker(config: Path = OptionalConfigOpt,
                   poll_seconds: int = typer.Option(10, min=2, max=3600),
                   once: bool = typer.Option(False, "--once"),
                   credentials_dir: Path = CredentialsDirOpt, data_dir: Path = DataDirOpt):
    """Process explicitly queued searches and content requests, and indexing the owner started in the UI.
    No automatic sync or model calls."""
    from towpath import request_worker as worker

    cfg, credentials_dir = _connector_setup(data_dir, config, credentials_dir)
    try:
        worker.run(cfg, poll_seconds=poll_seconds, once=once, credentials_dir=credentials_dir)
    except Exception as exc:  # noqa: BLE001 - never log account data from an exception message
        typer.echo(f"request worker stopped ({type(exc).__name__})", err=True)
        raise typer.Exit(1) from None


@connect_app.command("add-model")
def connect_add_model(connection: str, model: str,
                      destination: str = typer.Option(None, "--destination",
                                                      help="self-hosted (your network) or third-party (cloud)"),
                      config: Path = OptionalConfigOpt, credentials_dir: Path = CredentialsDirOpt,
                      data_dir: Path = DataDirOpt):
    """Test another model on an AI model connection's server with its stored token, and add it."""
    from towpath import connections

    cfg, credentials_dir = _connector_setup(data_dir, config, credentials_dir)
    try:
        tested = connections.add_model_name(cfg.store_dir, credentials_dir, connection, model, destination)
    except connections.ConnectionProblem as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from None
    typer.echo(f"{connection}:{model} answered in {tested['seconds']} s")


@connect_app.command("indexing")
def connect_indexing(source: str, action: str = typer.Argument(..., help="start (also continues) or pause"),
                     config: Path = OptionalConfigOpt, credentials_dir: Path = CredentialsDirOpt,
                     data_dir: Path = DataDirOpt):
    """Start, continue or pause a connection's indexing, as the Connections page's buttons do. The worker picks
    it up on its next poll."""
    from towpath import connections

    if action not in ("start", "pause"):
        raise typer.BadParameter("the action is start or pause")
    cfg, credentials_dir = _connector_setup(data_dir, config, credentials_dir)
    try:
        if action == "start":
            connections.start_indexing(cfg.store_dir, credentials_dir, source)
        else:
            connections.pause_indexing(cfg.store_dir, source)
    except connections.ConnectionProblem as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from None
    typer.echo(f"{source}: {connections.get(cfg.store_dir, source)['indexing']}")


def _print_progress(snapshot: dict) -> None:
    from towpath.progress import describe

    typer.echo(describe(snapshot), err=True)


def _stop_exit(exc) -> None:
    """Print a clean stop's sanitized reason and exit with its code."""
    typer.echo(f"stopped ({exc.code}): {exc.reason}", err=True)
    raise typer.Exit(exc.exit_code)


def _run_guarded(fn):
    """Run a Gmail-touching command: clean stops and Ctrl-C exit nonzero without tracebacks or URLs."""
    from towpath.adapters.errors import SyncStop

    try:
        return fn()
    except SyncStop as exc:
        _stop_exit(exc)
    except KeyboardInterrupt:
        typer.echo("cancelled; progress is saved and the next run resumes", err=True)
        raise typer.Exit(130) from None


@connect_app.command("serve-setup")
def serve_setup(config: Path = OptionalConfigOpt, credentials_dir: Path = CredentialsDirOpt,
                data_dir: Path = DataDirOpt,
                port: int = typer.Option(8791, min=1024, max=65535),
                host: str = typer.Option("127.0.0.1", "--host"),
                public_url: str = typer.Option(None, "--public-url",
                                               help="Address the reverse proxy serves, e.g. https://towpath.example.org.")):
    """Serve the connection setup pages (/connections/). Runs in towpath-connect, the only service that
    receives passwords; the reverse proxy sends /connections/ here and everything else to the web service."""
    cfg, credentials_dir = _connector_setup(data_dir, config, credentials_dir)
    if credentials_dir is None:
        raise typer.BadParameter("set --data-dir, or --credentials-dir, to the connector's private folder")
    credentials_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    if public_url is not None and not public_url.startswith(("https://", "http://")):
        raise typer.BadParameter("--public-url must start with https:// or http://")
    from towpath.web.application import launch

    typer.echo(f"Towpath connection setup is ready at {public_url or f'http://{host}:{port}'}/connections/")
    try:
        from towpath import connections

        connections.import_configured(cfg, credentials_dir)
        launch(cfg.store_dir, port, cfg, host, public_url, urlconf="towpath.web.connection_urls",
               TOWPATH_CREDENTIALS_DIR=credentials_dir.resolve(), TOWPATH_PUBLIC_URL=public_url)
    except KeyboardInterrupt:
        typer.echo("Connection setup stopped.")


@connect_app.command("sync")
def connect_sync(source: list[str] = typer.Argument(None, help="Source IDs; all sources if omitted."),
                 config: Path = ConfigOpt, as_json: bool = typer.Option(False, "--json"),
                 max_items: int = typer.Option(None, "--max-items", min=1,
                                               help="Stop after this many message reads; the next run resumes."),
                 quiet: bool = typer.Option(False, "--quiet", help="No progress lines on stderr."),
                 debug: bool = typer.Option(False, "--debug", help="Show tracebacks for unexpected errors."),
                 simulate_interrupt_after: int = typer.Option(None, hidden=True),
                 simulate_ignored_mask: bool = typer.Option(False, hidden=True)):
    """Index sources: full sync first, incremental after. Gmail requests are paced by the gmail_pacing settings.

    Exit codes: 0 complete or capped; 3 quota or daily budget stop; 4 authorization;
    5 permission; 6 rejected request; 7 server or network; 8 another command holds
    the budget lock; 130 cancelled; 1 unexpected error. Every stop keeps progress.
    """
    from towpath.progress import Progress

    cfg = _load(config)
    results, exit_code = [], 0
    for source_id in source or list(cfg.sources):
        options = {}
        if cfg.sources[source_id].adapter == "fixture-gmail":
            options = {"interrupt_after": simulate_interrupt_after, "ignore_mask": simulate_ignored_mask}
        tracker = Progress(source_id, None if quiet else _print_progress)
        try:
            result = _run_guarded(lambda sid=source_id, opts=options, tr=tracker: connect.sync(
                cfg, sid, max_items=max_items, progress=tr, **opts))
        except typer.Exit:
            raise
        except Exception as exc:  # noqa: BLE001 - recorded by sync; keep URLs and data off the terminal
            if debug:
                raise
            typer.echo(f"error: unexpected {exc.__class__.__name__}; progress is saved; "
                       f"see `towpath connect status {source_id}` (rerun with --debug for details)", err=True)
            raise typer.Exit(1) from None
        results.append(result)
        exit_code = max(exit_code, result.get("exit_code", 0))
    _emit(results, as_json)
    raise typer.Exit(exit_code)


@connect_app.command("status")
def connect_status(source: str, config: Path = ConfigOpt, runs: int = typer.Option(5, "--runs", min=1),
                   as_json: bool = typer.Option(False, "--json")):
    """Recent runs, resume state, and quota budget use. Shows no message data, IDs, or tokens."""
    _emit(connect.status(_load(config), source, runs), as_json)


@connect_app.command("probe")
def connect_probe(source: str, config: Path = ConfigOpt, as_json: bool = typer.Option(False, "--json")):
    """Show what a source reports about itself, without indexing anything (paced for Gmail)."""
    from towpath.adapters import build_connector

    cfg = _load(config)

    def run():
        connector = build_connector(cfg.sources[source], cfg)
        try:
            return {**connector.describe(), "probe": connector.probe()}
        finally:
            if hasattr(connector, "close"):
                connector.close()

    _emit(_run_guarded(run), as_json)


@connect_app.command("auth")
def connect_auth(source: str, config: Path = ConfigOpt,
                 no_browser: bool = typer.Option(False, "--no-browser", help="Print the URL instead.")):
    """Authorize read-only Gmail access with your own Google OAuth client."""
    from towpath.adapters.google_gmail import ScopeError, authorize

    cfg = _load(config)
    src = cfg.sources[source]
    if src.adapter != "gmail":
        raise typer.BadParameter(f"source {source} does not use the gmail adapter")
    try:
        result = authorize(src.client_secrets, src.token, open_browser=not no_browser)
    except ScopeError as exc:
        typer.echo(f"refused: {exc}", err=True)
        raise typer.Exit(2) from exc
    typer.echo(f"authorized; granted scopes: {', '.join(result['granted_scopes'])}; token saved to {result['token']}")


@connect_app.command("verify-structure")
def connect_verify(source: str, sample: int = typer.Option(25, "--sample", min=1), config: Path = ConfigOpt,
                   as_json: bool = typer.Option(False, "--json")):
    """Check that the structural field mask keeps body data out of responses (D16). Stores nothing."""
    from towpath.adapters import build_connector
    from towpath.adapters.google_gmail import verify_structure

    cfg = _load(config)

    def run():
        connector = build_connector(cfg.sources[source], cfg)
        try:
            return verify_structure(connector.client, connector.mask, sample)
        finally:
            connector.close()

    report = _run_guarded(run)
    _emit(report, as_json)
    raise typer.Exit(0 if report["passed"] else 1)


@connect_app.command("fetch-requests")
def connect_fetch(config: Path = ConfigOpt, as_json: bool = typer.Option(False, "--json")):
    """Fetch the parts that scans asked for, one at a time (paced for Gmail). Never runs automatically."""
    result = _run_guarded(lambda: connect.fetch_requests(_load(config)))
    _emit(result, as_json)
    raise typer.Exit(result.get("exit_code", 0))


@connect_app.command("coverage")
def connect_coverage(source: str, config: Path = ConfigOpt, as_json: bool = typer.Option(False, "--json")):
    """Show each run's completeness and counts for a source."""
    _emit(connect.coverage(_load(config), source), as_json)
