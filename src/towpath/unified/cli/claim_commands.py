"""``towpath claims``: validating claims and building a timeline."""

import json
from pathlib import Path

import typer

from towpath.unified.cli.search_commands import _emit, _fail

claims_app = typer.Typer(no_args_is_help=True, help="Timeline claims (towpath.claim/0): validate and order.")


def _claims_from(path: Path) -> list:
    text = path.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
        return data if isinstance(data, list) else [data]
    except ValueError:
        return [json.loads(line) for line in text.splitlines() if line.strip()]


@claims_app.command("validate")
def claims_validate(path: Path):
    """Validate claims (a JSON array, one object, or JSON Lines). Exit 1 if any is invalid."""
    from towpath.unified import claims

    try:
        documents = _claims_from(path)
    except (OSError, ValueError) as exc:
        _fail("invalid-request", f"cannot read claims: {exc}")
    report = [{"claim_id": d.get("claim_id") if isinstance(d, dict) else None,
               "problems": claims.validate(d) if isinstance(d, dict) else ["not an object"]} for d in documents]
    _emit({"claims": report, "valid": all(not r["problems"] for r in report)})
    if not all(not r["problems"] for r in report):
        raise typer.Exit(1)


@claims_app.command("timeline")
def claims_timeline(path: Path):
    """Order valid claims by date, keeping modality and every conflict visible."""
    from towpath.unified import claims

    try:
        parsed = [claims.Claim.from_dict(d) for d in _claims_from(path)]
    except (OSError, ValueError, KeyError) as exc:
        _fail("invalid-request", str(exc))
    _emit(claims.timeline(parsed))
