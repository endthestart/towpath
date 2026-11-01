"""The release scripts: collecting and checking source packages, and what anonymous clients can read."""

import json
import tarfile

import pytest

from tests.release_world import IMAGE_REPO, SOURCES_REPO, REV, FakeRegistry, World, assert_tags_resolve, make_work

import publish  # noqa: E402
import sources  # noqa: E402
from registry import RegistryError  # noqa: E402


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


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
