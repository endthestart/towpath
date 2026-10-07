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
    assert "packages: write" in publish and "actions: read" in publish and "contents: write" not in WORKFLOW
    # It runs only the standard-library release scripts from this commit, and builds nothing.
    assert "sparse-checkout: packaging/release" in publish and "persist-credentials: false" in publish
    assert not re.search(r"docker build(?!x)", publish) and "pip install" not in publish
    assert set(re.findall(r"secrets\.(\w+)", WORKFLOW)) == {"GITHUB_TOKEN"}
    assert "secrets." not in _job("image") and "secrets." not in _job("test")


def test_publishing_is_limited_to_trusted_refs_and_tested_images():
    condition = ("(github.event_name == 'push' && (github.ref == 'refs/heads/main' || startsWith(github.ref, "
                 "'refs/tags/v'))) || (github.event_name == 'workflow_dispatch' && inputs.publish)")
    assert condition in _job("publish") and condition in _job("image")
    assert "needs: image" in _job("publish") and "needs: test" in _job("image")
    image = _job("image")
    assert image.index("Test the candidate image") < image.index("Save the tested image")
    assert image.index("Save the tested image") < image.index("Collect and verify the corresponding source")
    publish = _job("publish")
    assert 'test "$tested" = "$loaded"' in publish and 'test "$tested_config" = "$saved_config"' in publish
    assert "packaging/release/publish.py" in publish and "--bundle" in publish
    assert "latest" not in publish


def test_image_contents_do_not_depend_on_the_triggering_ref():
    build = _job("image").split("- name: Build the candidate image", 1)[1].split("- name:", 1)[0]
    assert "GITHUB_REF" not in build and "github.ref" not in build
    assert 'REVISION="$GITHUB_SHA"' in build and "pyproject.toml" in build and "git log -1 --format=%cI" in build


def test_source_availability_is_checked_on_every_build():
    image = _job("image")
    assert "sources.py check" in image and "sources.py collect" in image
    assert "if: env.PUBLISH != 'true'" in image  # publishing builds run the stricter collect instead


def test_container_tests_use_disk_scratch():
    assert '--basetemp "$RUNNER_TEMP/container-tests"' in _job("image")
    assert "tmpfs" not in WORKFLOW


def test_images_record_licenses_and_drop_source_obliging_cpython_modules():
    assert DOCKERFILE.count("collect-licenses.sh") == 2
    assert "NOTICE-core.md" in DOCKERFILE and "NOTICE-recoll.md" in DOCKERFILE
    assert DOCKERFILE.count("apt-get upgrade") == 2
    assert "readline.*.so" in DOCKERFILE and "_gdbm.*.so" in DOCKERFILE and "_dbm.*.so" in DOCKERFILE
    assert "DISTRO_UPGRADE" not in WORKFLOW  # the offline-development switch is never used by CI


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
        # No real IP addresses; the unspecified listening address is not one.
        for address in re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", text):
            assert address == "0.0.0.0", (path, address)
        assert "/home/" not in text and "/Users/" not in text and "/volume1/" not in text, path
        assert not re.search(r"(?i)(token|password|secret)\s*=", text), path


def test_hub_example_keeps_credentials_away_from_the_web_service():
    compose = (DEPLOY / "compose.hub.example.yml").read_text()
    web = compose.split("\n  web:\n", 1)[1].split("\nnetworks:", 1)[0]
    mounts = re.findall(r"^\s+- (\S+):(\S+)$", web, re.M)
    assert mounts == [("${TOWPATH_DATA_DIR}/state", "/data/state")]  # never the whole folder or credentials/
    assert "connect-setup: {condition: service_healthy}" in web  # state/ exists before Docker could create it
    assert compose.count("<<: *runtime") == 3 and 'user: "${TOWPATH_USER:-568:568}"' in compose
    assert "build:" not in compose and "ports:" not in compose
    env = (DEPLOY / "env.hub.example").read_text()
    assert set(re.findall(r"^(TOWPATH_\w+)=", env, re.M)) == {
        "TOWPATH_IMAGE", "TOWPATH_RECOLL_IMAGE", "TOWPATH_DATA_DIR", "TOWPATH_PUBLIC_URL", "TOWPATH_PROXY_NETWORK",
        "TOWPATH_USER", "TOWPATH_LIBRARY_DIR", "TOWPATH_READ_GROUP"}
    assert compose.count(":/library:ro") == 2 and "/library" not in web  # never writable, never in the web service
