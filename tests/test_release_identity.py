"""The release scripts: promoting only a verified image, and never a binary without its source."""

import io
import tarfile

import pytest

from tests.release_world import IMAGE_REPO, SOURCE_URL, SOURCES_REPO, REV, World

import publish  # noqa: E402
import sources  # noqa: E402
from provenance import ProvenanceError  # noqa: E402


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


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
