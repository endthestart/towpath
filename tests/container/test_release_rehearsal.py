"""Rehearse publication with the real publish.py, real `docker push`, and a throwaway registry.

Runs against the candidate image (TOWPATH_IMAGE) in CI's image job, which holds no credentials.
The registry is the official `registry` image, pinned by digest and pulled through a public
mirror; GitHub's run API is a local stub. Every tag is checked independently with
`docker buildx imagetools`.
"""

import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "packaging" / "release"))
sys.path.insert(0, str(REPO / "tests"))

import sources  # noqa: E402
from test_release import make_work  # noqa: E402

IMAGE = os.environ.get("TOWPATH_IMAGE", "")
KIND = os.environ.get("TOWPATH_IMAGE_KIND", "core")
REGISTRY_IMAGE = os.environ.get(
    "TOWPATH_REHEARSAL_REGISTRY",
    "mirror.gcr.io/library/registry@sha256:a3d8aaa63ed8681a604f1dea0aa03f100d5895b6a58ace528858a7b332415373")
TITLE = "towpath" if KIND == "core" else "towpath-recoll"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class RunsStub:
    """GET /repos/<repo>/actions/runs/<id> and .../jobs, answering from ``self.runs``."""

    def __init__(self):
        self.runs = {}
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                parts = self.path.split("?")[0].strip("/").split("/")
                run = stub.runs.get(parts[5]) if len(parts) >= 6 else None
                if run is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                body = run["jobs"] if parts[-1] == "jobs" else run["run"]
                data = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def add(self, run_id: str, revision: str, image_ok: bool = True):
        self.runs[run_id] = {
            "run": {"head_sha": revision, "path": ".github/workflows/ci.yml", "event": "push",
                    "repository": {"full_name": "owner/towpath"}, "html_url": f"https://github.example/runs/{run_id}"},
            "jobs": {"jobs": [{"name": f"image ({KIND})", "conclusion": "success" if image_ok else "failure"}]},
        }


@pytest.fixture(scope="module")
def registry():
    subprocess.run(["docker", "pull", "-q", REGISTRY_IMAGE], check=True, capture_output=True, timeout=600)
    port = free_port()
    name = f"towpath-rehearsal-{port}"
    subprocess.run(["docker", "run", "-d", "--rm", "--name", name, "-p", f"127.0.0.1:{port}:5000", REGISTRY_IMAGE],
                   check=True, capture_output=True, timeout=120)
    for _ in range(50):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                break
        except OSError:
            time.sleep(0.2)
    yield f"localhost:{port}"
    subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=120)


def labels(image: str) -> dict:
    out = subprocess.run(["docker", "image", "inspect", image, "--format", "{{json .Config.Labels}}"],
                         check=True, capture_output=True, text=True).stdout
    return json.loads(out)


def config_digest(image: str, tmp: Path) -> str:
    archive = tmp / f"{abs(hash(image))}.tar"
    subprocess.run(["docker", "save", "-o", str(archive), image], check=True, timeout=600)
    try:
        return sources.config_digest_from_save(archive)
    finally:
        archive.unlink()


def resolve(reference: str) -> str | None:
    result = subprocess.run(["docker", "buildx", "imagetools", "inspect", reference, "--format", "{{json .Manifest}}"],
                            capture_output=True, text=True, timeout=120)
    return json.loads(result.stdout)["digest"] if result.returncode == 0 else None


@pytest.fixture(scope="module")
def world(registry, tmp_path_factory):
    tmp = tmp_path_factory.mktemp("rehearsal")
    found = labels(IMAGE)
    revision, source_url = found["org.opencontainers.image.revision"], found["org.opencontainers.image.source"]
    second = f"towpath-rehearsal-second-{KIND}"
    subprocess.run(["docker", "build", "-q", "-t", second, "-"], input=f"FROM {IMAGE}\nLABEL io.towpath.rehearsal=2\n",
                   text=True, check=True, capture_output=True, timeout=300)
    builds = {}
    for name, image, run_id in (("first", IMAGE, "101"), ("second", second, "202")):
        config = config_digest(image, tmp)
        work = make_work(tmp / f"work-{name}")
        found_sources, problems = sources.verify_collected(work)
        assert problems == []
        bundle = tmp / f"bundle-{name}"
        sources.assemble(work, bundle, {"target": KIND, "revision": revision, "image_config_digest": config,
                                        "source_repository": source_url, "run": {"id": run_id, "attempt": "1"}},
                         found_sources)
        builds[name] = {"image": image, "config": config, "bundle": bundle, "run": run_id}
    runs = RunsStub()
    for build in builds.values():
        runs.add(build["run"], revision)
    yield {"registry": registry, "revision": revision, "source_url": source_url, "builds": builds, "runs": runs,
           "tmp": tmp, "repo": f"{registry}/owner/{TITLE}", "sources_repo": f"{registry}/owner/towpath-sources"}
    runs.httpd.shutdown()
    subprocess.run(["docker", "rmi", "-f", second], capture_output=True)


def run_publish(world, build: str, ref: str, repo: str | None = None) -> subprocess.CompletedProcess:
    b = world["builds"][build]
    record = world["tmp"] / f"record-{build}-{ref.replace('/', '_')}.json"
    cmd = [sys.executable, str(REPO / "packaging" / "release" / "publish.py"), "--target", KIND,
           "--repository", repo or world["repo"], "--sources-repository", world["sources_repo"],
           "--local-image", b["image"], "--tested-config", b["config"], "--revision", world["revision"],
           "--ref", ref, "--source-url", world["source_url"], "--bundle", str(b["bundle"]),
           "--run-url", f"https://github.example/runs/{b['run']}", "--github-api", world["runs"].url,
           "--github-repository", "owner/towpath", "--record", str(record)]
    env = {k: v for k, v in os.environ.items() if k not in ("REGISTRY_USERNAME", "REGISTRY_PASSWORD")}
    result = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=1200)
    result.record = json.loads(record.read_text()) if record.exists() else None
    return result


def test_main_then_release_then_repeat(world):
    canonical_tag = f"{world['repo']}:sha-{world['revision']}"
    first = run_publish(world, "first", "refs/heads/main")
    assert first.returncode == 0, first.stderr
    digest = first.record["digest"]
    assert first.record["created_by_this_run"] and resolve(canonical_tag) == digest
    first_sources = f"{world['sources_repo']}:sha256-{world['builds']['first']['config'][7:]}"
    assert resolve(first_sources) == first.record["sources"]["digest"]

    release = run_publish(world, "second", "refs/tags/v0.1.0")
    assert release.returncode == 0, release.stderr
    assert not release.record["created_by_this_run"] and release.record["digest"] == digest
    assert release.record["built_and_tested_by"].endswith("/101")
    assert resolve(f"{world['repo']}:v0.1.0") == digest and resolve(canonical_tag) == digest
    assert resolve(f"{world['sources_repo']}:sha256-{world['builds']['second']['config'][7:]}") is None

    again = run_publish(world, "second", "refs/heads/main")
    assert again.returncode == 0, again.stderr
    assert again.record["digest"] == digest and resolve(canonical_tag) == digest


def test_conflicting_release_alias_is_left_alone(world):
    run_publish(world, "first", "refs/heads/main")
    taken = f"{world['repo']}:v0.2.0"
    subprocess.run(["docker", "tag", world["builds"]["second"]["image"], taken], check=True)
    subprocess.run(["docker", "push", "-q", taken], check=True, capture_output=True, timeout=600)
    before = resolve(taken)
    result = run_publish(world, "first", "refs/tags/v0.2.0")
    assert result.returncode == 1 and "already points at a different image" in result.stderr
    assert resolve(taken) == before


def test_unverifiable_builder_run_blocks_promotion(world):
    run_publish(world, "first", "refs/heads/main")
    world["runs"].add("101", world["revision"], image_ok=False)
    try:
        result = run_publish(world, "second", "refs/tags/v0.3.0")
    finally:
        world["runs"].add("101", world["revision"])
    assert result.returncode == 1 and "image (" in result.stderr
    assert resolve(f"{world['repo']}:v0.3.0") is None


def test_unreachable_or_refusing_registry_publishes_nothing(world):
    closed = f"localhost:{free_port()}/owner/{TITLE}"
    result = run_publish(world, "first", "refs/tags/v0.4.0", repo=closed)
    assert result.returncode == 1 and "error" in result.stderr.lower()

    class Refuse(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(401)
            self.send_header("WWW-Authenticate", f'Bearer realm="http://127.0.0.1:{port}/token",service="x"')
            self.send_header("Content-Length", "0")
            self.end_headers()

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Refuse)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        result = run_publish(world, "first", "refs/tags/v0.4.0", repo=f"127.0.0.1:{port}/owner/{TITLE}")
    finally:
        httpd.shutdown()
    assert result.returncode == 1 and "authentication" in result.stderr
    assert resolve(f"{world['repo']}:v0.4.0") is None
