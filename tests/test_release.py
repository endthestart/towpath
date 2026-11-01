"""Behaviour of the release scripts (packaging/release): publishing, and stopping before any write when it can't."""

import pytest

from tests.release_world import IMAGE_REPO, SOURCES_REPO, OTHER_REV, REV, World, assert_tags_resolve

import publish  # noqa: E402
from registry import RegistryError  # noqa: E402


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


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
