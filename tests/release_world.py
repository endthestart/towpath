"""In-memory fakes of a registry, Docker and runs, and bundles, for testing the release scripts."""

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "packaging" / "release"))

import publish  # noqa: E402
import sources  # noqa: E402
from provenance import ProvenanceError  # noqa: E402
from registry import Manifest, sha256_digest  # noqa: E402

IMAGE_REPO = "ghcr.io/owner/towpath-recoll"
SOURCES_REPO = "ghcr.io/owner/towpath-sources"
SOURCE_URL = "https://github.com/owner/towpath"
REV = "a" * 40
OTHER_REV = "b" * 40


class FakeRegistry:
    """Manifests by (repository, reference), blobs by (repository, digest), with injectable failures."""

    def __init__(self):
        self.manifests: dict[tuple[str, str], Manifest] = {}
        self.blobs: dict[tuple[str, str], bytes] = {}
        self.failures: dict[tuple, Exception] = {}
        self.writes: list[tuple] = []

    def _maybe_fail(self, *key):
        for probe in (key, key[:2], key[:1]):
            if probe in self.failures:
                raise self.failures.pop(probe)

    def get_manifest(self, repository, reference):
        self._maybe_fail("get", repository, reference)
        found = self.manifests.get((repository, reference))
        return found

    def put_manifest(self, repository, reference, raw, media_type):
        self._maybe_fail("put", repository, reference)
        manifest = Manifest(sha256_digest(raw), media_type, raw)
        self.manifests[(repository, reference)] = manifest
        self.manifests[(repository, manifest.digest)] = manifest
        self.writes.append(("manifest", repository, reference))
        return manifest.digest

    def blob_exists(self, repository, digest):
        self._maybe_fail("head", repository, digest)
        return (repository, digest) in self.blobs

    def get_blob(self, repository, digest):
        self._maybe_fail("blob", repository, digest)
        return self.blobs[(repository, digest)]

    def upload_blob(self, repository, payload, digest, size):
        self._maybe_fail("upload", repository, digest)
        data = payload if isinstance(payload, bytes) else Path(payload).read_bytes()
        assert sha256_digest(data) == digest and len(data) == size
        if (repository, digest) not in self.blobs:
            self.blobs[(repository, digest)] = data
            self.writes.append(("blob", repository, digest))

    def resolve(self, repository, reference) -> str | None:
        found = self.manifests.get((repository, reference))
        return found.digest if found else None


class FakeDocker:
    """Local images by name: a config JSON; push writes a single-platform manifest like docker push."""

    def __init__(self, registry: FakeRegistry):
        self.registry, self.images, self.pushed = registry, {}, []
        self.fail_next = None

    def add(self, name: str, revision: str = REV, title: str = "towpath-recoll", source: str = SOURCE_URL,
            build: str = "1") -> str:
        config = json.dumps({"architecture": "amd64", "os": "linux", "build": build, "config": {"Labels": {
            "org.opencontainers.image.revision": revision, "org.opencontainers.image.source": source,
            "org.opencontainers.image.title": title}}}, sort_keys=True).encode()
        self.images[name] = config
        return sha256_digest(config)

    def push(self, local, remote):
        if self.fail_next:
            exc, self.fail_next = self.fail_next, None
            raise exc
        repository, _, tag = remote.rpartition(":")
        config = self.images[local]
        self.registry.blobs[(repository, sha256_digest(config))] = config
        raw = json.dumps({"schemaVersion": 2, "mediaType": "application/vnd.oci.image.manifest.v1+json",
                          "config": {"digest": sha256_digest(config), "size": len(config),
                                     "mediaType": "application/vnd.oci.image.config.v1+json"},
                          "layers": []}).encode()
        self.registry.put_manifest(repository, tag, raw, "application/vnd.oci.image.manifest.v1+json")
        self.pushed.append(remote)


class FakeRuns:
    def __init__(self):
        self.good: dict[str, tuple[str, str]] = {}
        self.calls = []

    def verify(self, run_id, revision, target):
        self.calls.append(run_id)
        if self.good.get(run_id) != (revision, target):
            raise ProvenanceError(f"run {run_id} did not build and test {target} at {revision}")
        return f"https://github.example/runs/{run_id}"


# ------------------------------------------------------------------- bundles


def make_work(path: Path, packages=(("libfoo1", "1.0-1", "foo", "1.0-1"),)) -> Path:
    """A collected-sources work folder as sources.py's container step leaves it."""
    (path / "sources").mkdir(parents=True)
    (path / "licenses" / "bundled").mkdir(parents=True)
    rows = []
    for package, version, src, src_version in packages:
        rows.append("\t".join((package, version, src, src_version)))
        (path / "licenses" / "bundled" / package).mkdir()
        (path / "licenses" / "bundled" / package / "copyright").write_text(f"License: GPL-2+\n{package}\n")
        upstream = sources.strip_epoch(src_version).split("-")[0]
        orig = path / "sources" / f"{src}_{upstream}.orig.tar.gz"
        orig.write_bytes(f"{src} upstream source".encode())
        debian = path / "sources" / f"{src}_{sources.strip_epoch(src_version)}.debian.tar.xz"
        debian.write_bytes(f"{src} packaging".encode())
        lines = "".join(f" {sources.sha256_file(f)} {f.stat().st_size} {f.name}\n" for f in (orig, debian))
        (path / "sources" / f"{src}_{sources.strip_epoch(src_version)}.dsc").write_text(
            f"-----BEGIN PGP SIGNED MESSAGE-----\nHash: SHA512\n\nFormat: 3.0 (quilt)\nSource: {src}\n"
            f"Version: {src_version}\nChecksums-Sha256:\n{lines}\n-----BEGIN PGP SIGNATURE-----\nxyz\n"
            "-----END PGP SIGNATURE-----\n")
    tsv = "\n".join(sorted(rows)) + "\n"
    (path / "dpkg.tsv").write_text(tsv)
    (path / "image-packages.tsv").write_text(tsv)
    (path / "licenses" / "bundled" / "packages.tsv").write_text(tsv)
    (path / "licenses" / "common-licenses").mkdir()
    (path / "licenses" / "common-licenses" / "GPL-2").write_text("GNU GENERAL PUBLIC LICENSE Version 2\n")
    (path / "os-release.txt").write_text("Synthetic Linux 1\n")
    (path / "foreign-links.tsv").write_text("/usr/local/bin/python3\t/lib/x86_64-linux-gnu/libc.so.6\n")
    (path / "failed.tsv").write_text("")
    return path


def make_bundle(base: Path, config: str, revision: str = REV, target: str = "recoll", run_id: str = "101") -> Path:
    work = make_work(base / f"work-{config[7:15]}-{run_id}")
    found, problems = sources.verify_collected(work)
    assert problems == []
    bundle = base / f"bundle-{config[7:15]}-{run_id}"
    sources.assemble(work, bundle, {"target": target, "revision": revision, "image_config_digest": config,
                                    "source_repository": SOURCE_URL, "run": {"id": run_id, "attempt": "1"}}, found)
    return bundle


class World:
    def __init__(self, tmp_path):
        self.tmp = tmp_path
        self.registry = FakeRegistry()
        self.docker = FakeDocker(self.registry)
        self.runs = FakeRuns()

    def build(self, name: str, run_id: str, revision: str = REV, **labels) -> tuple[str, Path]:
        """A tested image from one workflow run, with its source bundle; the run is recorded as passing."""
        config = self.docker.add(name, revision=revision, build=name, **labels)
        self.runs.good[run_id] = (revision, "recoll")
        return config, make_bundle(self.tmp, config, revision, run_id=run_id)

    def ctx(self, name, config, bundle, ref="refs/heads/main", revision=REV, run_id="101") -> publish.Context:
        return publish.Context("recoll", IMAGE_REPO, SOURCES_REPO, name, config, revision, ref, SOURCE_URL,
                               "towpath-recoll", bundle, f"https://github.example/runs/{run_id}")

    def publish(self, ctx):
        return publish.publish(ctx, self.registry, self.docker, self.runs)


def assert_tags_resolve(world: World, record: dict):
    for tag in record["tags"]:
        assert world.registry.resolve(IMAGE_REPO, tag) == record["digest"], tag
