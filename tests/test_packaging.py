"""Contracts for container packaging, CI publishing, and the deployment example (text checks, no Docker)."""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WORKFLOW = (REPO / ".github" / "workflows" / "ci.yml").read_text()
DOCKERFILE = (REPO / "Dockerfile").read_text()
DEPLOY = REPO / "deploy"


def _job(name: str) -> str:
    """The text of one top-level job in the workflow."""
    match = re.search(rf"^  {name}:\n(.*?)(?=^  [a-z][a-z0-9_-]*:\n|\Z)", WORKFLOW, re.S | re.M)
    assert match, f"no job {name}"
    return match.group(1)


def test_every_action_is_pinned_to_a_full_commit():
    uses = re.findall(r"uses:\s*(\S+)", WORKFLOW)
    assert uses
    for ref in uses:
        assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", ref), ref


def test_only_the_publish_job_can_write_packages():
    assert re.search(r"^permissions:\n  contents: read\n", WORKFLOW, re.M)
    assert WORKFLOW.count("packages: write") == 1
    publish = _job("publish")
    assert "packages: write" in publish
    assert "actions/checkout" not in publish  # it runs no repository code
    assert not re.search(r"docker build(?!x)", publish)  # it never rebuilds
    assert "secrets.GITHUB_TOKEN" in publish
    assert re.findall(r"secrets\.(\w+)", WORKFLOW) == ["GITHUB_TOKEN"]


def test_publishing_is_limited_to_trusted_refs_and_tested_images():
    condition = ("(github.event_name == 'push' && (github.ref == 'refs/heads/main' || startsWith(github.ref, "
                 "'refs/tags/v'))) || (github.event_name == 'workflow_dispatch' && inputs.publish)")
    assert condition in _job("publish") and condition in _job("image")
    assert "needs: image" in _job("publish") and "needs: test" in _job("image")
    image = _job("image")
    assert image.index("Test the candidate image") < image.index("Save the tested image")
    publish = _job("publish")
    assert 'test "$tested" = "$loaded"' in publish
    assert "never overwritten" in publish
    assert "sha-$GITHUB_SHA" in publish and "latest" not in publish


def test_container_tests_use_disk_scratch():
    assert '--basetemp "$RUNNER_TEMP/container-tests"' in _job("image")
    assert "tmpfs" not in WORKFLOW


def test_images_are_pinned_unprivileged_clis():
    for base in re.findall(r"^ARG (?:PYTHON|UBUNTU)_IMAGE=(\S+)", DOCKERFILE, re.M):
        assert re.search(r"@sha256:[0-9a-f]{64}$", base), base
    assert DOCKERFILE.count("USER 10001:10001") == 2
    assert 'ENTRYPOINT ["towpath"]' in DOCKERFILE and 'CMD ["--help"]' in DOCKERFILE
    assert "EXPOSE" not in DOCKERFILE and "HEALTHCHECK" not in DOCKERFILE


def test_deployment_files_only_pull_pinned_images():
    compose = (DEPLOY / "compose.example.yml").read_text()
    assert not re.search(r"^\s*build:", compose, re.M)
    assert "${TOWPATH_IMAGE:?" in compose
    env = (DEPLOY / "env.example").read_text()
    assert re.search(r"^TOWPATH_IMAGE=ghcr\.io/OWNER/towpath-recoll@sha256:", env, re.M)
    for setting in ("network_mode: none", "read_only: true", 'user: "10001:10001"', "cap_drop: [ALL]",
                    "no-new-privileges:true", 'restart: "no"'):
        assert setting in compose, setting
    assert re.search(r'recoll-index:\n(?:.*\n)*?\s+profiles: \["index"\]', compose)  # indexing is manual
    assert compose.count(":/data/roots/archive:ro") == 2


def test_deployment_examples_hold_no_private_values():
    for path in DEPLOY.iterdir():
        text = path.read_text()
        assert not re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", text), path  # no IP addresses
        assert "/home/" not in text and "/Users/" not in text and "/volume1/" not in text, path
        assert not re.search(r"(?i)(token|password|secret)\s*=", text), path
