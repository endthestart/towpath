"""``towpath provider``: the mail-management provider (Inbox Zero), read-only."""

from pathlib import Path

import typer

from towpath.cli.common import ConfigOpt, _emit, _load

provider_app = typer.Typer(no_args_is_help=True, help="Mail-management provider (Inbox Zero), read-only.")


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
