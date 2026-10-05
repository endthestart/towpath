"""Behaviour of the release scripts (packaging/release) against in-memory registry, Docker, and runs."""

import io
import json
import sys
import tarfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "packaging" / "release"))

import publish  # noqa: E402
import sources  # noqa: E402
from provenance import ProvenanceError  # noqa: E402
from registry import Manifest, RegistryError, sha256_digest  # noqa: E402

IMAGE_REPO = "ghcr.io/owner/towpath-recoll"
SOURCES_REPO = "ghcr.io/owner/towpath-sources"
SOURCE_URL = "https://github.com/owner/towpath"
REV = "a" * 40
OTHER_REV = "b" * 40


# ------------------------------------------------------------------- fakes


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


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


def assert_tags_resolve(world: World, record: dict):
    for tag in record["tags"]:
        assert world.registry.resolve(IMAGE_REPO, tag) == record["digest"], tag


# ------------------------------------------------------------------- publication sequences


def test_main_then_release_promotes_the_main_image(world):
    main_config, main_bundle = world.build("main-build", "101")
    first = world.publish(world.ctx("main-build", main_config, main_bundle))
    assert first["created_by_this_run"] and first["tags"] == [f"sha-{REV}"]
    assert first["config_digest"] == main_config
    assert world.registry.resolve(SOURCES_REPO, f"sha256-{main_config[7:]}") == first["sources"]["digest"]

    release_config, release_bundle = world.build("release-build", "202")  # the tag run built again
    assert release_config != main_config
    writes_before = len(world.registry.writes)
    second = world.publish(world.ctx("release-build", release_config, release_bundle, ref="refs/tags/v0.1.0",
                                     run_id="202"))
    assert not second["created_by_this_run"] and second["digest"] == first["digest"]
    assert second["tags"] == [f"sha-{REV}", "v0.1.0"] and second["config_digest"] == main_config
    assert second["built_and_tested_by"].endswith("/101") and world.runs.calls == ["101"]
    assert world.registry.writes[writes_before:] == [("manifest", IMAGE_REPO, "v0.1.0")]  # only the alias
    assert world.docker.pushed == [f"{IMAGE_REPO}:sha-{REV}"]  # the release build was never pushed
    assert world.registry.resolve(SOURCES_REPO, f"sha256-{release_config[7:]}") is None
    assert_tags_resolve(world, second)


def test_first_publication_through_a_release_tag(world):
    config, bundle = world.build("tag-build", "303")
    record = world.publish(world.ctx("tag-build", config, bundle, ref="refs/tags/v1.0.0", run_id="303"))
    assert record["created_by_this_run"] and record["tags"] == [f"sha-{REV}", "v1.0.0"]
    assert_tags_resolve(world, record)
    sources_manifest = world.registry.manifests[(SOURCES_REPO, f"sha256-{config[7:]}")]
    assert sources_manifest.body["annotations"]["io.towpath.image.config"] == config
    titles = {layer["annotations"]["org.opencontainers.image.title"] for layer in sources_manifest.body["layers"]}
    assert {"manifest.json", "SHA256SUMS", "README.md", "licenses.tar", "sources/foo_1.0-1.dsc"} <= titles


def test_repeated_main_publication_is_a_no_op(world):
    config, bundle = world.build("main-build", "101")
    first = world.publish(world.ctx("main-build", config, bundle))
    again_config, again_bundle = world.build("rerun-build", "102")
    writes = len(world.registry.writes)
    again = world.publish(world.ctx("rerun-build", again_config, again_bundle, run_id="102"))
    assert again["digest"] == first["digest"] and not again["created_by_this_run"]
    assert len(world.registry.writes) == writes


def test_release_retry_after_alias_write_failed(world):
    config, bundle = world.build("main-build", "101")
    world.publish(world.ctx("main-build", config, bundle))
    world.registry.failures[("put", IMAGE_REPO, "v0.2.0")] = RegistryError("ghcr.io: HTTP 502")
    tag_config, tag_bundle = world.build("tag-build", "202")
    with pytest.raises(RegistryError):
        world.publish(world.ctx("tag-build", tag_config, tag_bundle, ref="refs/tags/v0.2.0", run_id="202"))
    assert world.registry.resolve(IMAGE_REPO, "v0.2.0") is None
    retry_config, retry_bundle = world.build("retry-build", "203")
    record = world.publish(world.ctx("retry-build", retry_config, retry_bundle, ref="refs/tags/v0.2.0",
                                     run_id="203"))
    assert record["config_digest"] == config and record["tags"] == [f"sha-{REV}", "v0.2.0"]
    assert_tags_resolve(world, record)


def test_retry_after_sources_published_but_image_push_failed(world):
    config, bundle = world.build("first-build", "101")
    world.docker.fail_next = publish.PublishError("docker push failed: connection reset")
    with pytest.raises(publish.PublishError):
        world.publish(world.ctx("first-build", config, bundle))
    assert world.registry.resolve(IMAGE_REPO, f"sha-{REV}") is None  # no binary without its source, nor yet
    orphan = world.registry.resolve(SOURCES_REPO, f"sha256-{config[7:]}")
    assert orphan is not None
    retry_config, retry_bundle = world.build("retry-build", "102")
    record = world.publish(world.ctx("retry-build", retry_config, retry_bundle, run_id="102"))
    assert record["created_by_this_run"] and record["config_digest"] == retry_config
    assert world.registry.resolve(SOURCES_REPO, f"sha256-{retry_config[7:]}") == record["sources"]["digest"]
    assert_tags_resolve(world, record)


def test_retry_after_partial_source_upload_reuses_blobs(world):
    config, bundle = world.build("first-build", "101")
    world.registry.failures[("put", SOURCES_REPO)] = RegistryError("ghcr.io: connection reset")
    with pytest.raises(RegistryError):
        world.publish(world.ctx("first-build", config, bundle))
    assert world.registry.resolve(IMAGE_REPO, f"sha-{REV}") is None and world.docker.pushed == []
    blobs_after_failure = len(world.registry.blobs)
    record = world.publish(world.ctx("first-build", config, bundle))
    assert record["created_by_this_run"] and len(world.registry.blobs) == blobs_after_failure + 1  # + image config
    assert_tags_resolve(world, record)


def test_conflicting_alias_stops_before_any_write(world):
    other = world.docker.add("someone-else", revision=OTHER_REV, build="x")
    world.docker.push("someone-else", f"{IMAGE_REPO}:v0.3.0")
    taken = world.registry.resolve(IMAGE_REPO, "v0.3.0")
    writes = len(world.registry.writes)
    config, bundle = world.build("tag-build", "303")
    with pytest.raises(publish.PublishError, match="refusing to publish a new image under an existing release"):
        world.publish(world.ctx("tag-build", config, bundle, ref="refs/tags/v0.3.0", run_id="303"))
    assert len(world.registry.writes) == writes and world.registry.resolve(IMAGE_REPO, "v0.3.0") == taken
    assert other

    main_config, main_bundle = world.build("main-build", "101")
    world.publish(world.ctx("main-build", main_config, main_bundle))
    writes = len(world.registry.writes)
    with pytest.raises(publish.PublishError, match="already points at a different image"):
        world.publish(world.ctx("tag-build", config, bundle, ref="refs/tags/v0.3.0", run_id="303"))
    assert len(world.registry.writes) == writes and world.registry.resolve(IMAGE_REPO, "v0.3.0") == taken


@pytest.mark.parametrize("error", [RegistryError("ghcr.io/v2/owner/towpath-recoll/manifests/sha-a: HTTP 401"),
                                   RegistryError("ghcr.io/v2/...: HTTP 403 DENIED"),
                                   RegistryError("ghcr.io/v2/...: [Errno 111] Connection refused")])
def test_registry_errors_stop_before_any_write(world, error):
    config, bundle = world.build("main-build", "101")
    world.registry.failures[("get", IMAGE_REPO, f"sha-{REV}")] = error
    with pytest.raises(RegistryError):
        world.publish(world.ctx("main-build", config, bundle, ref="refs/tags/v1.0.0"))
    assert world.registry.writes == [] and world.docker.pushed == []


def test_error_while_checking_an_alias_stops_before_any_write(world):
    config, bundle = world.build("main-build", "101")
    world.registry.failures[("get", IMAGE_REPO, "v1.0.0")] = RegistryError("timed out")
    with pytest.raises(RegistryError):
        world.publish(world.ctx("main-build", config, bundle, ref="refs/tags/v1.0.0"))
    assert world.registry.writes == []


# ------------------------------------------------------------------- identity and provenance of a reused image


def published(world, **labels):
    config, bundle = world.build("main-build", "101", **labels)
    return world.publish(world.ctx("main-build", config, bundle)), config


def test_existing_image_with_wrong_labels_is_not_promoted(world):
    stranger = world.docker.add("stranger", revision=REV, source="https://github.com/elsewhere/fork", build="s")
    world.docker.push("stranger", f"{IMAGE_REPO}:sha-{REV}")
    config, bundle = world.build("tag-build", "202")
    with pytest.raises(publish.PublishError, match="not this revision's towpath-recoll image"):
        world.publish(world.ctx("tag-build", config, bundle, ref="refs/tags/v1.0.0", run_id="202"))
    assert world.registry.resolve(IMAGE_REPO, "v1.0.0") is None and stranger


def test_existing_image_without_source_is_not_promoted(world):
    record, config = published(world)
    del world.registry.manifests[(SOURCES_REPO, f"sha256-{config[7:]}")]
    tag_config, tag_bundle = world.build("tag-build", "202")
    with pytest.raises(publish.PublishError, match="has no corresponding source"):
        world.publish(world.ctx("tag-build", tag_config, tag_bundle, ref="refs/tags/v1.0.0", run_id="202"))
    assert world.registry.resolve(IMAGE_REPO, "v1.0.0") is None and record


def test_existing_image_with_missing_source_blob_is_not_promoted(world):
    record, config = published(world)
    manifest = world.registry.manifests[(SOURCES_REPO, f"sha256-{config[7:]}")]
    del world.registry.blobs[(SOURCES_REPO, manifest.body["layers"][-1]["digest"])]
    tag_config, tag_bundle = world.build("tag-build", "202")
    with pytest.raises(publish.PublishError, match="is missing"):
        world.publish(world.ctx("tag-build", tag_config, tag_bundle, ref="refs/tags/v1.0.0", run_id="202"))
    assert record


def test_existing_image_whose_run_cannot_be_verified_is_not_promoted(world):
    record, _ = published(world)
    del world.runs.good["101"]  # for example a run from another workflow, commit, or a failed image job
    tag_config, tag_bundle = world.build("tag-build", "202")
    with pytest.raises(ProvenanceError):
        world.publish(world.ctx("tag-build", tag_config, tag_bundle, ref="refs/tags/v1.0.0", run_id="202"))
    assert world.registry.resolve(IMAGE_REPO, "v1.0.0") is None and record


# ------------------------------------------------------------------- source requirements before a first publication


def test_no_binary_without_a_matching_bundle(world):
    config, bundle = world.build("main-build", "101")
    with pytest.raises(publish.PublishError, match="never published without source"):
        world.publish(world.ctx("main-build", config, None))
    other_config, other_bundle = world.build("other-build", "102")
    with pytest.raises(publish.PublishError, match="not the tested image"):
        world.publish(world.ctx("main-build", config, other_bundle))
    assert world.registry.writes == [] and world.docker.pushed == [] and bundle


def test_tampered_bundle_is_refused_before_any_write(world):
    config, bundle = world.build("main-build", "101")
    victim = next((bundle / "sources").glob("*.orig.tar.gz"))
    victim.write_bytes(b"altered")
    with pytest.raises(sources.SourceError):
        world.publish(world.ctx("main-build", config, bundle))
    assert world.registry.writes == []


def test_conflicting_existing_source_artifact_stops_before_any_write(world):
    config, bundle = world.build("main-build", "101")
    world.registry.put_manifest(SOURCES_REPO, f"sha256-{config[7:]}", b'{"schemaVersion":2,"layers":[]}',
                                publish.OCI_MANIFEST)
    writes = len(world.registry.writes)
    with pytest.raises(publish.PublishError, match="already holds different content"):
        world.publish(world.ctx("main-build", config, bundle))
    assert len(world.registry.writes) == writes and world.docker.pushed == []


def test_sources_artifact_is_reproducible(world):
    config, bundle = world.build("main-build", "101")
    first = publish.build_sources_artifact(bundle, REV, SOURCE_URL)
    second = publish.build_sources_artifact(bundle, REV, SOURCE_URL)
    assert first.digest == second.digest and first.manifest_raw == second.manifest_raw


def test_downloaded_source_artifact_matches_documented_checksum_layout(world):
    """An OCI client saves layers by title; the documented untar/check steps must work."""
    config, bundle = world.build("main-build", "101")
    artifact = publish.build_sources_artifact(bundle, REV, SOURCE_URL)
    downloaded = world.tmp / "downloaded"
    downloaded.mkdir()
    for descriptor, payload in artifact.layers:
        name = descriptor["annotations"]["org.opencontainers.image.title"]
        path = downloaded / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload if isinstance(payload, bytes) else payload.read_bytes())
    with tarfile.open(fileobj=io.BytesIO((downloaded / "licenses.tar").read_bytes())) as tar:
        tar.extractall(downloaded, filter="data")
    for line in (downloaded / "SHA256SUMS").read_text().splitlines():
        expected, _, name = line.partition("  ")
        path = downloaded / name
        assert path.is_file(), f"documented checksum path is missing after pull: {name}"
        assert sources.sha256_file(path) == expected


# ------------------------------------------------------------------- sources.py checks


def test_collected_sources_are_checked_against_dsc_and_package_list(tmp_path):
    work = make_work(tmp_path / "w", (("libfoo1", "1:1.0-1", "foo", "1:1.0-1"),
                                      ("foo-bin", "1:1.0-1+b1", "foo", "1:1.0-1"),
                                      ("libbar2", "2.0-3", "bar", "2.0-3")))
    found, problems = sources.verify_collected(work)
    assert problems == [] and {(s["source"], s["version"]) for s in found} == {("foo", "1:1.0-1"), ("bar", "2.0-3")}
    assert all(len(s["files"]) == 3 for s in found)  # .dsc + orig + debian

    (work / "sources" / "bar_2.0-3.debian.tar.xz").write_bytes(b"tampered")
    (work / "sources" / "stray.tar.gz").write_bytes(b"?")
    (work / "image-packages.tsv").write_text("libfoo1\t1:1.0-1\tfoo\t1:1.0-1\n")
    (work / "failed.tsv").write_text("baz\t9-9\n")
    (work / "foreign-links.tsv").write_text(
        "/usr/local/lib/python3.12/lib-dynload/readline.so\t/lib/x86_64-linux-gnu/libreadline.so.8\n"
        "/usr/local/lib/python3.12/lib-dynload/_dbm.so\t/lib/x86_64-linux-gnu/libdb-5.3.so\n")
    text = "\n".join(sources.verify_collected(work)[1])
    for expected in ("does not match its .dsc checksum", "unexpected file in sources: stray.tar.gz",
                     "recorded package list differs", "baz=9-9 is not available", "libreadline.so.8",
                     "libdb-5.3.so"):
        assert expected in text, expected


def test_version_mismatch_and_missing_dsc_are_problems(tmp_path):
    work = make_work(tmp_path / "w")
    dsc = work / "sources" / "foo_1.0-1.dsc"
    dsc.write_text(dsc.read_text().replace("Version: 1.0-1", "Version: 1.0-2"))
    assert any("describes foo=1.0-2" in p for p in sources.verify_collected(work)[1])
    dsc.unlink()
    assert any("foo_1.0-1.dsc was not downloaded" in p for p in sources.verify_collected(work)[1])


def test_availability_check_reads_print_uris(tmp_path):
    work = make_work(tmp_path / "w")
    (work / "uris").mkdir()
    (work / "uris" / "foo_1.0-1.txt").write_text(
        "'http://archive.example/pool/f/foo/foo_1.0-1.dsc' foo_1.0-1.dsc 1723 SHA512:abc\n"
        "'http://archive.example/pool/f/foo/foo_1.0.orig.tar.gz' foo_1.0.orig.tar.gz 318862 SHA512:def\n")
    assert sources.verify_check(work) == []
    (work / "uris" / "foo_1.0-1.txt").write_text("")
    assert any("does not list foo_1.0-1.dsc" in p for p in sources.verify_check(work))


def test_bundle_verification_catches_added_and_removed_files(world):
    config, bundle = world.build("main-build", "101")
    sources.verify_bundle(bundle)
    (bundle / "licenses" / "extra.txt").write_text("unlisted")
    with pytest.raises(sources.SourceError, match="differ from SHA256SUMS"):
        sources.verify_bundle(bundle)
    (bundle / "licenses" / "extra.txt").unlink()
    manifest = json.loads((bundle / "manifest.json").read_text())
    manifest["packages"].append({"package": "x", "version": "1", "source": "x", "source_version": "1"})
    (bundle / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(sources.SourceError):
        sources.verify_bundle(bundle)


def test_config_digest_is_read_from_docker_save(tmp_path):
    for config_path in ("blobs/sha256/" + "c" * 64, "c" * 64 + ".json"):
        archive = tmp_path / f"save-{len(config_path)}.tar"
        manifest = json.dumps([{"Config": config_path, "RepoTags": ["x:y"], "Layers": []}]).encode()
        with tarfile.open(archive, "w") as tar:
            info = tarfile.TarInfo("manifest.json")
            info.size = len(manifest)
            import io
            tar.addfile(info, io.BytesIO(manifest))
        assert sources.config_digest_from_save(archive) == "sha256:" + "c" * 64


class FakeAnonymous:
    """What a client without credentials can read: only repositories listed as public."""

    def __init__(self, registry: FakeRegistry, public: set):
        self.registry, self.public = registry, public

    def get_manifest(self, repository, reference):
        if repository not in self.public:
            raise RegistryError(f"{repository}: HTTP 401 UNAUTHORIZED", status=401)
        return self.registry.get_manifest(repository, reference)


@pytest.mark.parametrize("failed_repo", [IMAGE_REPO, SOURCES_REPO])
def test_unknown_anonymous_visibility_blocks_binary_publication(world, failed_repo):
    config, bundle = world.build("main-build", "101")

    class UnreachableAnonymous(FakeAnonymous):
        def get_manifest(self, repository, reference):
            if repository == failed_repo:
                raise RegistryError("anonymous registry probe timed out")
            return super().get_manifest(repository, reference)

    with pytest.raises(RegistryError, match="timed out"):
        publish.publish(world.ctx("main-build", config, bundle), world.registry, world.docker,
                        world.runs, UnreachableAnonymous(world.registry, {IMAGE_REPO, SOURCES_REPO}))
    assert world.docker.pushed == []


def test_public_image_with_private_source_is_refused(world):
    config, bundle = world.build("main-build", "101")
    anonymous = FakeAnonymous(world.registry, {IMAGE_REPO})
    with pytest.raises(publish.PublishError, match="make the source package public"):
        publish.publish(world.ctx("main-build", config, bundle), world.registry, world.docker, world.runs, anonymous)
    assert world.docker.pushed == [] and world.registry.resolve(IMAGE_REPO, f"sha-{REV}") is None

    record = publish.publish(world.ctx("main-build", config, bundle), world.registry, world.docker, world.runs,
                             FakeAnonymous(world.registry, set()))  # both private: consistent
    assert record["sources"]["visibility"] == {"checked": True, "image_public": False, "sources_public": False}
    tag_config, tag_bundle = world.build("tag-build", "202")
    with pytest.raises(publish.PublishError, match="make the source package public"):
        publish.publish(world.ctx("tag-build", tag_config, tag_bundle, ref="refs/tags/v1.0.0", run_id="202"),
                        world.registry, world.docker, world.runs, FakeAnonymous(world.registry, {IMAGE_REPO}))
    assert world.registry.resolve(IMAGE_REPO, "v1.0.0") is None
    both = publish.publish(world.ctx("tag-build", tag_config, tag_bundle, ref="refs/tags/v1.0.0", run_id="202"),
                           world.registry, world.docker, world.runs,
                           FakeAnonymous(world.registry, {IMAGE_REPO, SOURCES_REPO}))
    assert both["sources"]["visibility"]["image_public"] and both["sources"]["visibility"]["sources_public"]
    assert_tags_resolve(world, both)
