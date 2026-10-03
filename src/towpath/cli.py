"""Towpath command line (read-only).

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

app = typer.Typer(no_args_is_help=True, add_completion=False,
                  help="Towpath: read-only tools for mail, documents, photos, and model endpoints.")
connect_app = typer.Typer(no_args_is_help=True, help="towpath-connect: read sources, fetch requested content.")
scan_app = typer.Typer(no_args_is_help=True, help="towpath-worker: scans and presence checks.")
proposals_app = typer.Typer(no_args_is_help=True, help="Inspect and export proposals.")
item_app = typer.Typer(no_args_is_help=True, help="Record decisions about items.")
fixtures_app = typer.Typer(no_args_is_help=True, help="Generate and change synthetic fixtures.")
model_app = typer.Typer(no_args_is_help=True, help="Model endpoints: probe, grant, and run queued work.")
provider_app = typer.Typer(no_args_is_help=True, help="Mail-management provider (Inbox Zero), read-only.")
config_app = typer.Typer(no_args_is_help=True, help="Check configuration without contacting any service.")
files_app = typer.Typer(no_args_is_help=True,
                        help="Optional file discovery through an existing search tool (towpath-connect; read-only).")
app.add_typer(connect_app, name="connect")
app.add_typer(scan_app, name="scan")
app.add_typer(proposals_app, name="proposals")
app.add_typer(item_app, name="item")
app.add_typer(fixtures_app, name="fixtures")
app.add_typer(model_app, name="model")
app.add_typer(provider_app, name="provider")
app.add_typer(config_app, name="config")
app.add_typer(files_app, name="files")

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


@fixtures_app.command("files")
def fixtures_files(out: Path):
    """Generate the synthetic file discovery corpus, its fixture catalog, and an example config."""
    from towpath.discovery import corpus

    summary = corpus.generate(out)
    summary["config"] = str(corpus.write_example_config(out))
    _emit(summary, False)


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
    pacing = cfg.gmail_pacing
    typer.echo(f"gmail pacing: budget '{pacing.budget_id}', at least {pacing.min_interval_seconds} s between "
               f"requests, {pacing.units_per_minute} units/min, {pacing.daily_units} units/day "
               f"({pacing.day_timezone} day)")
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


def _files(fn):
    """Run a files command: JSON out; clean, sanitized errors; Ctrl-C records the run as interrupted."""
    from towpath.discovery import service

    try:
        typer.echo(json.dumps(fn(service), indent=2, sort_keys=True))
    except KeyboardInterrupt:
        typer.echo("cancelled; the run is recorded as interrupted", err=True)
        raise typer.Exit(130) from None
    except Exception as exc:
        code = service.error_code(exc)
        if code == "error" and not isinstance(exc, (service.DiscoveryError, ValueError)):
            raise
        typer.echo(f"error ({code}): {exc}", err=True)
        raise typer.Exit(2) from None


@files_app.command("status")
def files_status(config: Path = ConfigOpt):
    """Configuration, grants, and catalog state. Calls no provider."""
    cfg = _load(config)
    _files(lambda s: s.status(cfg))


@files_app.command("probe")
def files_probe(provider: str = typer.Argument(None), config: Path = ConfigOpt):
    """Ask each configured provider for its tool and version, and list its capabilities."""
    cfg = _load(config)
    _files(lambda s: s.probe(cfg, provider))


@files_app.command("search")
def files_search(query: str, provider: str = typer.Option(None, "--provider"),
                 limit: int = typer.Option(None, "--limit", help="Results per page (capped by max_results)."),
                 offset: int = typer.Option(0, "--offset"), config: Path = ConfigOpt):
    """Bounded search of roots with a search grant. Results carry locations, never file text."""
    cfg = _load(config)
    _files(lambda s: s.search(cfg, query, provider, limit, offset))


@files_app.command("describe")
def files_describe(occurrence: str, config: Path = ConfigOpt):
    """One result's full record, and whether it is current, changed, or unavailable now."""
    cfg = _load(config)
    _files(lambda s: s.describe(cfg, occurrence))


@files_app.command("excerpt")
def files_excerpt(occurrence: str, at: int = typer.Option(0, "--at", help="Start offset in the extracted text."),
                  max_bytes: int = typer.Option(None, "--max-bytes"), config: Path = ConfigOpt):
    """A bounded text read of one result (needs an excerpt grant). Records a citation."""
    cfg = _load(config)
    _files(lambda s: s.excerpt(cfg, occurrence, at, max_bytes))


@files_app.command("cite")
def files_cite(citation: str, config: Path = ConfigOpt):
    """Check a citation: current, stale, unavailable, or unverifiable. Never serves changed content."""
    cfg = _load(config)
    _files(lambda s: s.resolve_citation(cfg, citation))


@files_app.command("recover")
def files_recover(occurrence: str, config: Path = ConfigOpt):
    """Write a derived copy of one result to recover_dir, with provenance (needs a recover grant)."""
    cfg = _load(config)
    _files(lambda s: s.recover(cfg, occurrence))


@files_app.command("import")
def files_import(provider: str = typer.Option(None, "--provider"), root: str = typer.Option(None, "--root"),
                 max_items: int = typer.Option(10000, "--max-items"), config: Path = ConfigOpt):
    """Record references (never text) for granted roots in files.db, with honest coverage."""
    cfg = _load(config)
    _files(lambda s: s.import_catalog(cfg, provider, root, max_items))


@files_app.command("grant")
def files_grant(root: str, feature: str, config: Path = ConfigOpt):
    """Grant one feature on one root: search, excerpt, recover, agent-context, or life-evidence (web role)."""
    from towpath.discovery import policy

    cfg = _load(config)
    _files(lambda s: (policy.grant(cfg, root, feature), {"root": root, "granted": feature})[1])


@files_app.command("revoke")
def files_revoke(root: str, feature: str, config: Path = ConfigOpt):
    """Withdraw one feature grant on one root (web role)."""
    from towpath.discovery import policy

    cfg = _load(config)
    _files(lambda s: {"root": root, "revoked": feature, "was_granted": policy.revoke(cfg, root, feature)})


@files_app.command("evaluate")
def files_evaluate(out: Path, tool: list[str] = typer.Option(["recoll", "sist2"], "--tool"),
                   timeout: float = typer.Option(120, "--timeout", help="Seconds per tool call."),
                   recoll_python: str = typer.Option(None, "--recoll-python",
                                                     help="Interpreter with Recoll's Python binding."),
                   sist2_binary: str = typer.Option(None, "--sist2-binary"),
                   sist2_image: str = typer.Option(None, "--sist2-image", help="A local sist2 image."),
                   keep: bool = typer.Option(False, "--keep", help="Keep the temporary corpus and indexes.")):
    """Run installed providers over a generated synthetic corpus; write OUT/report.json. Needs no config."""
    from towpath.discovery.evaluate import evaluate

    bad = set(tool) - {"recoll", "sist2"}
    if bad:
        raise typer.BadParameter(f"unknown tool(s): {', '.join(sorted(bad))}")
    report = evaluate(out, tuple(tool), timeout, recoll_python, sist2_binary, sist2_image, keep)
    for name, result in report["tools"].items():
        state = result.get("version", "available") if result["available"] else f"unavailable ({result['reason']})"
        typer.echo(f"{name}: {state}")
    typer.echo(f"report: {out / 'report.json'}")
