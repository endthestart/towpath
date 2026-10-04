"""Check, through the GitHub API, that a workflow run produced and tested an image.

Used before promoting an already published canonical image: its source bundle names the run that
built it, and that run must belong to this repository and workflow, be for the same commit, come
from a trusted event, and have passed the image job (build and container tests) for the target.
"""

import json
import urllib.error
import urllib.request

TRUSTED_EVENTS = {"push", "workflow_dispatch"}


class ProvenanceError(RuntimeError):
    pass


class GitHubRuns:
    def __init__(self, api_url: str, repository: str, token: str | None, workflow_path: str, timeout: float = 30):
        self.api_url, self.repository, self.token = api_url.rstrip("/"), repository, token
        self.workflow_path, self.timeout = workflow_path, timeout

    def _get(self, path: str) -> dict:
        request = urllib.request.Request(f"{self.api_url}/repos/{self.repository}{path}",
                                         headers={"Accept": "application/vnd.github+json",
                                                  "X-GitHub-Api-Version": "2022-11-28"})
        if self.token:
            request.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            raise ProvenanceError(f"GitHub API {path}: HTTP {exc.code}") from None
        except (urllib.error.URLError, OSError) as exc:
            raise ProvenanceError(f"GitHub API {path}: {getattr(exc, 'reason', exc)}") from None

    def verify(self, run_id: str, revision: str, target: str) -> str:
        """Return the run's URL if it built and tested ``target`` at ``revision``; raise otherwise."""
        if not str(run_id).isdigit():
            raise ProvenanceError(f"source bundle names no workflow run (got {run_id!r})")
        run = self._get(f"/actions/runs/{run_id}")
        problems = []
        if run.get("head_sha") != revision:
            problems.append(f"run is for {run.get('head_sha')}, not {revision}")
        if run.get("path", "").split("@")[0] != self.workflow_path:
            problems.append(f"run is from {run.get('path')}, not {self.workflow_path}")
        if run.get("event") not in TRUSTED_EVENTS:
            problems.append(f"run was triggered by {run.get('event')}")
        if (run.get("repository") or {}).get("full_name", self.repository) != self.repository:
            problems.append("run belongs to another repository")
        jobs = self._get(f"/actions/runs/{run_id}/jobs?filter=all&per_page=100").get("jobs", [])
        image_jobs = [j for j in jobs if j.get("name") == f"image ({target})"]
        if not any(j.get("conclusion") == "success" for j in image_jobs):
            problems.append(f"run has no successful 'image ({target})' job")
        if problems:
            raise ProvenanceError(f"run {run_id}: " + "; ".join(problems))
        return run.get("html_url", f"run {run_id}")
