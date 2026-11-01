"""``towpath config``: check configuration without contacting any service."""

from pathlib import Path

import typer

from towpath.cli.common import ConfigOpt, _load

config_app = typer.Typer(no_args_is_help=True, help="Check configuration without contacting any service.")


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
        elif s.adapter == "imap":
            boxes = ", ".join(s.mailboxes) if s.mailboxes else "all selectable mailboxes"
            detail = f"{s.host}:{s.port} ({s.security}), password {secret(s.credential)}, {boxes}"
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
