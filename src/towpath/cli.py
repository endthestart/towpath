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
model_app = typer.Typer(no_args_is_help=True, help="Model endpoints: probe, grant, and run queued work.")
provider_app = typer.Typer(no_args_is_help=True, help="Mail-management provider (Inbox Zero), read-only.")
config_app = typer.Typer(no_args_is_help=True, help="Check configuration without contacting any service.")
app.add_typer(connect_app, name="connect")
app.add_typer(scan_app, name="scan")
app.add_typer(proposals_app, name="proposals")
app.add_typer(item_app, name="item")
app.add_typer(fixtures_app, name="fixtures")
app.add_typer(model_app, name="model")
app.add_typer(provider_app, name="provider")
app.add_typer(config_app, name="config")

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
                 max_items: int = typer.Option(None, "--max-items",
                                               help="Stop after this many messages; the next run resumes."),
                 simulate_interrupt_after: int = typer.Option(None, hidden=True),
                 simulate_ignored_mask: bool = typer.Option(False, hidden=True)):
    """Index sources: full sync first, incremental after."""
    cfg = _load(config)
    results = []
    for source_id in source or list(cfg.sources):
        options = {}
        if cfg.sources[source_id].adapter == "fixture-gmail":
            options = {"interrupt_after": simulate_interrupt_after, "ignore_mask": simulate_ignored_mask}
        results.append(connect.sync(cfg, source_id, max_items=max_items, **options))
    _emit(results, as_json)


@connect_app.command("probe")
def connect_probe(source: str, config: Path = ConfigOpt, as_json: bool = typer.Option(False, "--json")):
    """Show what a source reports about itself, without indexing anything."""
    from towpath.adapters import build_connector

    connector = build_connector(_load(config).sources[source])
    _emit({**connector.describe(), "probe": connector.probe()}, as_json)


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
def connect_verify(source: str, sample: int = typer.Option(25, "--sample"), config: Path = ConfigOpt,
                   as_json: bool = typer.Option(False, "--json")):
    """Check that the structural field mask keeps body data out of responses (D16). Stores nothing."""
    from towpath.adapters import build_connector
    from towpath.adapters.google_gmail import verify_structure

    cfg = _load(config)
    connector = build_connector(cfg.sources[source])
    report = verify_structure(connector.client, connector.mask, sample)
    _emit(report, as_json)
    raise typer.Exit(0 if report["passed"] else 1)


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


@model_app.command("grant")
def model_grant(endpoint: str, data_class: str, config: Path = ConfigOpt):
    """Allow a data class on a named endpoint's current profile (web role decision)."""
    try:
        fp = decisions.grant_model(_load(config), endpoint, data_class)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"granted {data_class} on {endpoint} (profile {fp}); changing the profile voids this grant")


@model_app.command("run-queue")
def model_run_queue(config: Path = ConfigOpt, as_json: bool = typer.Option(False, "--json")):
    """Process queued model work through the gateway (worker)."""
    from towpath.models import gateway

    _emit(gateway.run_queue(_load(config)), as_json)


@model_app.command("check")
def model_check(task: str, data_class: str = typer.Option("metadata", "--data-class"), config: Path = ConfigOpt):
    """Show whether the gateway would allow a task, without calling any model."""
    from towpath.models import gateway

    status, reason, endpoint = gateway.check_policy(_load(config), task, data_class, [])
    typer.echo(f"{status}: {reason or 'allowed'} (endpoint {getattr(endpoint, 'id', None)})")


def _provider(config: Path, provider: str | None):
    from towpath.providers.inbox_zero import InboxZeroProvider

    cfg = _load(config)
    if not cfg.providers:
        raise typer.BadParameter("no [[providers]] configured")
    pid = provider or next(iter(cfg.providers))
    return InboxZeroProvider(cfg.providers[pid])


@provider_app.command("probe")
def provider_probe(provider: str = typer.Argument(None), config: Path = ConfigOpt,
                   as_json: bool = typer.Option(False, "--json")):
    """Check which read-only capabilities the provider key allows."""
    p = _provider(config, provider)
    _emit({**p.describe(), "capabilities": p.probe()}, as_json)


@provider_app.command("overview")
def provider_overview(provider: str = typer.Argument(None), period: str = typer.Option("week", "--period"),
                      config: Path = ConfigOpt):
    """Statistics from the provider's API."""
    _emit(_provider(config, provider).overview(period), True)


@provider_app.command("rules")
def provider_rules(provider: str = typer.Argument(None), config: Path = ConfigOpt):
    """The provider's rules, read-only."""
    _emit(_provider(config, provider).rules(), True)


@provider_app.command("links")
def provider_links(provider: str = typer.Argument(None), config: Path = ConfigOpt):
    """Links to the provider's own screens for features without a public API."""
    _emit(_provider(config, provider).links(), False)


@config_app.command("check")
def config_check(config: Path = ConfigOpt):
    """Validate the config and report which secrets are present. Never prints a secret or calls a service."""
    from towpath import credentials
    from towpath.models.profiles import ProfileError, check_destination

    cfg = _load(config)
    problems = 0

    def secret(ref):
        nonlocal problems
        try:
            credentials.resolve(ref)
            return "present"
        except credentials.CredentialError as exc:
            problems += 1
            return f"MISSING ({exc})"

    typer.echo(f"stores: {cfg.store_dir}")
    for s in cfg.sources.values():
        if s.adapter == "gmail":
            state = []
            for label, path in (("client secrets", s.client_secrets), ("token", s.token)):
                ok = path is not None and path.is_file()
                problems += not ok and label == "client secrets"
                state.append(f"{label} {'present' if ok else 'missing'}")
            detail = ", ".join(state)
        elif s.credential:
            detail = f"{s.base_url}, credential {secret(s.credential)}"
        else:
            detail = str(s.path)
        typer.echo(f"source {s.id}: {s.adapter} ({s.kind}) - {detail}")
    for e in cfg.endpoints.values():
        try:
            check_destination(e, cfg.bundled_hosts)
            dest = e.destination
        except ProfileError as exc:
            problems += 1
            dest = f"REJECTED ({exc})"
        cred = "none" if e.credential == "none" else secret(e.credential)
        typer.echo(f"endpoint {e.id}: {e.kind} {e.model} at {e.base_url} - {dest}; credential {cred}; "
                   f"allows {', '.join(e.allow_data)}")
    for task, eid in cfg.tasks.items():
        typer.echo(f"task {task} -> {eid}")
    for p in cfg.providers.values():
        typer.echo(f"provider {p.id}: {p.adapter} at {p.base_url}; api key {secret(p.api_key)}")
    typer.echo("ok" if not problems else f"{problems} problem(s)")
    raise typer.Exit(1 if problems else 0)
