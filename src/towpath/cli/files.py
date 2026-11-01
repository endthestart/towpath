"""``towpath files``: optional file discovery. Discovery is imported only when a command runs."""

import json
from pathlib import Path

import typer

from towpath.cli.common import ConfigOpt, _load

files_app = typer.Typer(no_args_is_help=True,
                        help="Optional file discovery through an existing search tool (towpath-connect; read-only).")


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


@files_app.command("context")
def files_context(purpose: str = typer.Option(..., "--purpose", help="agent-context or life-evidence."),
                  query: str = typer.Option(None, "--query"),
                  occurrence: list[str] = typer.Option([], "--occurrence", help="Repeat for several."),
                  max_items: int = typer.Option(None, "--max-items"),
                  excerpt_bytes: int = typer.Option(None, "--excerpt-bytes"),
                  provider: str = typer.Option(None, "--provider"),
                  out: Path = typer.Option(None, "--out", help="Write the packet here instead of stdout."),
                  config: Path = ConfigOpt):
    """Build a versioned, bounded context packet (towpath.files.context/1). Calls no model."""
    from towpath.discovery import context

    cfg = _load(config)

    def build(_service):
        packet = context.build(cfg, purpose, query, tuple(occurrence), max_items, excerpt_bytes, provider)
        if out is None:
            return packet
        out.write_text(json.dumps(packet, indent=2, sort_keys=True))
        return {"written": str(out), "items": len(packet["items"]), "omitted": len(packet["omitted"])}

    _files(build)
