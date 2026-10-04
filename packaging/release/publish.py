"""Publish one tested image per revision and target, and promote it to release tags by digest.

Model:

- The **canonical** image for a revision and target is ``<repository>:sha-<commit>``. The first
  trusted run that reaches publication pushes its tested image there; that tag is never changed.
- A **release alias** (``vX.Y.Z`` from a tag ref) is the canonical manifest written under another
  tag: the same bytes, so the same digest. Nothing is rebuilt or re-pushed for a release.
- The canonical image's **corresponding source** is published first, as the OCI artifact
  ``<sources repository>:sha256-<image config hex>``, and must exist before an image is promoted.
- When the canonical image already exists (a release tag after a main publish, or a retry), it is
  verified before promotion: labels name this revision, source repository, and image; its source
  artifact exists and names the same image config; and the workflow run recorded there built and
  tested it (checked through the GitHub API). This run's own build is then not published.

Every tag the run may touch is read first; a registry, authentication, or network error, or a
conflicting tag, stops the run before anything is written. The last step re-reads every tag and
requires each to resolve to the canonical digest.
"""

import argparse
import io
import json
import os
import subprocess
import sys
import tarfile
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from provenance import GitHubRuns, ProvenanceError  # noqa: E402
from registry import HttpRegistry, Manifest, RegistryError, image_config, sha256_digest  # noqa: E402
from sources import SourceError, sha256_file, verify_bundle  # noqa: E402

SOURCES_ARTIFACT_TYPE = "application/vnd.towpath.sources.v1"
SOURCES_CONFIG_TYPE = "application/vnd.towpath.sources.manifest.v1+json"
OCI_MANIFEST = "application/vnd.oci.image.manifest.v1+json"


class PublishError(RuntimeError):
    pass


@dataclass
class Context:
    target: str                 # "core" or "recoll"
    repository: str             # ghcr.io/<owner>/towpath or .../towpath-recoll
    sources_repository: str     # ghcr.io/<owner>/towpath-sources
    local_image: str            # the tested image, loaded locally
    tested_config: str          # config digest of the tested image
    revision: str
    ref: str
    source_url: str             # expected org.opencontainers.image.source
    title: str                  # expected org.opencontainers.image.title
    bundle: Path | None = None  # the tested image's source bundle
    run_url: str = ""
    log: list = field(default_factory=list)

    @property
    def canonical_tag(self) -> str:
        return f"sha-{self.revision}"

    @property
    def aliases(self) -> list[str]:
        if self.ref.startswith("refs/tags/v"):
            return [self.ref[len("refs/tags/"):]]
        return []


class DockerCli:
    def push(self, local: str, remote: str) -> None:
        for cmd in (["docker", "tag", local, remote], ["docker", "push", remote]):
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                raise PublishError(f"{' '.join(cmd[:2])} failed: {result.stderr.strip()[-1000:]}")


# ------------------------------------------------------------------- source artifact


def _licenses_tar(bundle: Path) -> bytes:
    """licenses/ as a deterministic tar (sorted, fixed owner and times), so retries match byte for byte."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for path in sorted(p for p in (bundle / "licenses").rglob("*")):
            info = tar.gettarinfo(str(path), arcname=path.relative_to(bundle).as_posix())
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mtime = 0
            info.mode = 0o755 if path.is_dir() else 0o644
            if path.is_file():
                with open(path, "rb") as f:
                    tar.addfile(info, f)
            else:
                tar.addfile(info)
    return buf.getvalue()


@dataclass
class SourcesArtifact:
    manifest_raw: bytes
    config_raw: bytes
    layers: list            # (descriptor, path or bytes)
    meta: dict

    @property
    def digest(self) -> str:
        return sha256_digest(self.manifest_raw)


def build_sources_artifact(bundle: Path, revision: str, source_url: str) -> SourcesArtifact:
    meta = verify_bundle(bundle)
    config_raw = (bundle / "manifest.json").read_bytes()
    layers = []

    def descriptor(name, media_type, digest, size):
        return {"mediaType": media_type, "digest": digest, "size": size,
                "annotations": {"org.opencontainers.image.title": name}}

    for name in ("README.md", "SHA256SUMS", "manifest.json"):
        path = bundle / name
        layers.append((descriptor(name, "application/octet-stream", "sha256:" + sha256_file(path),
                                  path.stat().st_size), path))
    licenses = _licenses_tar(bundle)
    layers.append((descriptor("licenses.tar", "application/vnd.oci.image.layer.v1.tar", sha256_digest(licenses),
                              len(licenses)), licenses))
    for source in meta["sources"]:
        for entry in source["files"]:
            layers.append((descriptor(entry["name"], "application/octet-stream", "sha256:" + entry["sha256"],
                                      entry["size"]), bundle / "sources" / entry["name"]))
    manifest = {
        "schemaVersion": 2, "mediaType": OCI_MANIFEST, "artifactType": SOURCES_ARTIFACT_TYPE,
        "config": {"mediaType": SOURCES_CONFIG_TYPE, "digest": sha256_digest(config_raw), "size": len(config_raw)},
        "layers": [d for d, _ in layers],
        "annotations": {"org.opencontainers.image.revision": revision, "org.opencontainers.image.source": source_url,
                        "io.towpath.image.config": meta["image_config_digest"],
                        "io.towpath.image.target": meta["target"]},
    }
    raw = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    return SourcesArtifact(raw, config_raw, layers, meta)


def push_sources(registry, repository: str, tag: str, artifact: SourcesArtifact) -> str:
    registry.upload_blob(repository, artifact.config_raw, sha256_digest(artifact.config_raw), len(artifact.config_raw))
    for descriptor, payload in artifact.layers:
        registry.upload_blob(repository, payload, descriptor["digest"], descriptor["size"])
    digest = registry.put_manifest(repository, tag, artifact.manifest_raw, OCI_MANIFEST)
    if digest != artifact.digest:
        raise PublishError(f"{repository}:{tag} stored as {digest}, expected {artifact.digest}")
    return digest


def check_sources_complete(registry, repository: str, manifest: Manifest) -> None:
    body = manifest.body
    for descriptor in [body["config"], *body.get("layers", [])]:
        if not registry.blob_exists(repository, descriptor["digest"]):
            raise PublishError(f"{repository}@{manifest.digest}: blob {descriptor['digest']} is missing")


# ------------------------------------------------------------------- image checks


def check_labels(ctx: Context, config: dict, where: str) -> None:
    labels = (config.get("config") or {}).get("Labels") or {}
    expected = {"org.opencontainers.image.revision": ctx.revision,
                "org.opencontainers.image.source": ctx.source_url,
                "org.opencontainers.image.title": ctx.title}
    wrong = {k: labels.get(k) for k, v in expected.items() if labels.get(k) != v}
    if wrong:
        raise PublishError(f"{where} is not this revision's {ctx.title} image (labels: {wrong})")


def hex_of(digest: str) -> str:
    return digest.split(":", 1)[1]


def anonymously_readable(anonymous, repository: str, reference: str) -> bool:
    """Can a client without credentials read ``repository``? (None, i.e. "not found", still counts.)"""
    try:
        anonymous.get_manifest(repository, reference)
        return True
    except RegistryError:
        return False


def check_source_reachable(ctx: Context, anonymous, image_tag: str, sources_tag: str) -> dict:
    """Refuse when anyone can pull the image but not its source (for example only the image was made public)."""
    if anonymous is None:
        return {"checked": False}
    image_public = anonymously_readable(anonymous, ctx.repository, image_tag)
    sources_public = anonymously_readable(anonymous, ctx.sources_repository, sources_tag)
    if image_public and not sources_public:
        raise PublishError(f"{ctx.repository} can be pulled without credentials but {ctx.sources_repository} "
                           "cannot; make the source package public before publishing binaries")
    return {"checked": True, "image_public": image_public, "sources_public": sources_public}


def publish(ctx: Context, registry, docker, runs, anonymous=None) -> dict:
    repo, canonical_tag = ctx.repository, ctx.canonical_tag

    # Preflight: read every tag this run could touch. Errors and conflicts stop here, before writes.
    canonical = registry.get_manifest(repo, canonical_tag)
    alias_state = {alias: registry.get_manifest(repo, alias) for alias in ctx.aliases}

    if canonical is None:
        stray = [a for a, m in alias_state.items() if m is not None]
        if stray:
            raise PublishError(f"{repo}:{', '.join(stray)} exists but {canonical_tag} does not; refusing to "
                               "publish a new image under an existing release tag")
        if ctx.bundle is None:
            raise PublishError("no source bundle for the tested image; binaries are never published without source")
        artifact = build_sources_artifact(ctx.bundle, ctx.revision, ctx.source_url)
        meta = artifact.meta
        if (meta.get("image_config_digest"), meta.get("revision"), meta.get("target")) != (
                ctx.tested_config, ctx.revision, ctx.target):
            raise PublishError(f"source bundle is for {meta.get('target')} {meta.get('image_config_digest')} at "
                               f"{meta.get('revision')}, not the tested image {ctx.tested_config}")
        sources_tag = f"sha256-{hex_of(ctx.tested_config)}"
        existing_sources = registry.get_manifest(ctx.sources_repository, sources_tag)
        if existing_sources is not None and existing_sources.digest != artifact.digest:
            raise PublishError(f"{ctx.sources_repository}:{sources_tag} already holds different content")

        # Writes: corresponding source first, then the binary.
        sources_digest = push_sources(registry, ctx.sources_repository, sources_tag, artifact)
        stored_sources = registry.get_manifest(ctx.sources_repository, sources_tag)
        if stored_sources is None or stored_sources.digest != sources_digest:
            raise PublishError(f"{ctx.sources_repository}:{sources_tag} does not resolve to {sources_digest}")
        check_sources_complete(registry, ctx.sources_repository, stored_sources)
        ctx.log.append(f"published source {ctx.sources_repository}@{sources_digest}")
        visibility = check_source_reachable(ctx, anonymous, canonical_tag, sources_tag)

        docker.push(ctx.local_image, f"{repo}:{canonical_tag}")
        canonical = registry.get_manifest(repo, canonical_tag)
        if canonical is None:
            raise PublishError(f"{repo}:{canonical_tag} is missing after the push")
        config_digest, config = image_config(registry, repo, canonical)
        if config_digest != ctx.tested_config:
            raise PublishError(f"{repo}:{canonical_tag} holds {config_digest}, not the tested {ctx.tested_config}")
        check_labels(ctx, config, f"{repo}:{canonical_tag}")
        created, built_by, sources_meta = True, ctx.run_url, meta
        ctx.log.append(f"published canonical {repo}@{canonical.digest}")
    else:
        config_digest, config = image_config(registry, repo, canonical)
        check_labels(ctx, config, f"{repo}:{canonical_tag}")
        sources_tag = f"sha256-{hex_of(config_digest)}"
        stored_sources = registry.get_manifest(ctx.sources_repository, sources_tag)
        if stored_sources is None:
            raise PublishError(f"{repo}:{canonical_tag} has no corresponding source at "
                               f"{ctx.sources_repository}:{sources_tag}; it will not be promoted")
        sources_meta = json.loads(registry.get_blob(ctx.sources_repository, stored_sources.body["config"]["digest"]))
        if (sources_meta.get("image_config_digest"), sources_meta.get("revision"), sources_meta.get("target")) != (
                config_digest, ctx.revision, ctx.target):
            raise PublishError(f"{ctx.sources_repository}:{sources_tag} does not describe {repo}:{canonical_tag}")
        check_sources_complete(registry, ctx.sources_repository, stored_sources)
        built_by = runs.verify(str((sources_meta.get("run") or {}).get("id", "")), ctx.revision, ctx.target)
        conflicts = [a for a, m in alias_state.items() if m is not None and m.digest != canonical.digest]
        if conflicts:
            raise PublishError(f"{repo}:{', '.join(conflicts)} already points at a different image")
        visibility = check_source_reachable(ctx, anonymous, canonical_tag, sources_tag)
        sources_digest = stored_sources.digest
        created = False
        ctx.log.append(f"reusing canonical {repo}@{canonical.digest} built by {built_by}; this run's build "
                       f"({ctx.tested_config}) is not published")

    # Release aliases: the canonical manifest's exact bytes under each new tag.
    for alias, current in alias_state.items():
        if current is None:
            registry.put_manifest(repo, alias, canonical.raw, canonical.media_type)
            ctx.log.append(f"tagged {repo}:{alias}")

    # Every tag must resolve to the canonical digest.
    for tag in [canonical_tag, *ctx.aliases]:
        found = registry.get_manifest(repo, tag)
        if found is None or found.digest != canonical.digest:
            raise PublishError(f"{repo}:{tag} resolves to {found.digest if found else 'nothing'}, "
                               f"not {canonical.digest}")

    return {
        "image": repo, "digest": canonical.digest, "reference": f"{repo}@{canonical.digest}",
        "config_digest": config_digest, "canonical_tag": canonical_tag, "aliases": ctx.aliases,
        "tags": [canonical_tag, *ctx.aliases], "created_by_this_run": created, "built_and_tested_by": built_by,
        "this_run_tested_config": ctx.tested_config, "revision": ctx.revision, "ref": ctx.ref,
        "run": ctx.run_url,
        "sources": {"repository": ctx.sources_repository, "tag": sources_tag, "digest": sources_digest,
                    "reference": f"{ctx.sources_repository}@{sources_digest}",
                    "totals": sources_meta.get("totals"), "os": sources_meta.get("os"),
                    "visibility": visibility},
        "log": ctx.log,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Publish a tested image and its release aliases.")
    parser.add_argument("--target", required=True, choices=["core", "recoll"])
    parser.add_argument("--repository", required=True)
    parser.add_argument("--sources-repository", required=True)
    parser.add_argument("--local-image", required=True)
    parser.add_argument("--tested-config", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--run-url", default="")
    parser.add_argument("--github-api", default=os.environ.get("GITHUB_API_URL", "https://api.github.com"))
    parser.add_argument("--github-repository", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--workflow-path", default=".github/workflows/ci.yml")
    parser.add_argument("--record", type=Path, required=True)
    args = parser.parse_args(argv)
    title = "towpath" if args.target == "core" else "towpath-recoll"
    ctx = Context(args.target, args.repository, args.sources_repository, args.local_image, args.tested_config,
                  args.revision, args.ref, args.source_url, title, args.bundle, args.run_url)
    registry = HttpRegistry(os.environ.get("REGISTRY_USERNAME"), os.environ.get("REGISTRY_PASSWORD"))
    runs = GitHubRuns(args.github_api, args.github_repository, os.environ.get("GITHUB_TOKEN"), args.workflow_path)
    try:
        record = publish(ctx, registry, DockerCli(), runs, anonymous=HttpRegistry(None, None))
    except (PublishError, RegistryError, ProvenanceError, SourceError) as exc:
        for line in ctx.log:
            print(line)
        print(f"::error::{exc}" if os.environ.get("GITHUB_ACTIONS") else f"error: {exc}", file=sys.stderr)
        return 1
    args.record.write_text(json.dumps(record, indent=2, sort_keys=True))
    for line in record["log"]:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
