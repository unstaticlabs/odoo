#!/usr/bin/env python3
"""Resolve one explicitly selected deployed release artifact from GitHub Actions."""

# ruff: noqa: EM101, T201 - release CLI reports concise fail-closed decisions.

from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
import tempfile
import urllib.request
from urllib.parse import urlsplit
import zipfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from continuous_operations_contracts import validate_commit  # noqa: E402
from distribution_release import ReleaseArtifactError, validate  # noqa: E402


class PriorReleaseInputError(ValueError):
    """The configured deployed release cannot safely be used as an input."""


class _AuthSafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, *args, **kwargs):  # noqa: ANN001, ANN201
        redirected = super().redirect_request(request, *args, **kwargs)
        if redirected is not None and (
            urlsplit(redirected.full_url).netloc != urlsplit(request.full_url).netloc
        ):
            redirected.remove_header("Authorization")
        return redirected


def _open(request: urllib.request.Request, timeout: int):
    if urlsplit(request.full_url).scheme != "https":
        raise PriorReleaseInputError("GitHub API requests must use HTTPS")
    return urllib.request.build_opener(_AuthSafeRedirectHandler()).open(
        request, timeout=timeout,
    )


def _get_json(url: str, token: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with _open(request, timeout=30) as response:
        return json.load(response)


def _get_bytes(url: str, token: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with _open(request, timeout=60) as response:
        return response.read()


def resolve(
    *, api_url: str, repository: str, run_id: int, token: str, to_commit: str,
) -> dict[str, Any]:
    validate_commit(to_commit, "to_commit")
    run_url = f"{api_url}/repos/{repository}/actions/runs/{run_id}"
    run = _get_json(run_url, token)
    expected_run = {
        "event": "push",
        "head_branch": "19-usl",
        "conclusion": "success",
        "path": ".github/workflows/product-image.yml",
    }
    for key, expected in expected_run.items():
        if run.get(key) != expected:
            raise PriorReleaseInputError(
                f"Actions run {run_id} has {key}={run.get(key)!r}, expected {expected!r}",
            )
    head_sha = validate_commit(run.get("head_sha"), "run.head_sha")
    ancestry = subprocess.run(
        ["git", "merge-base", "--is-ancestor", head_sha, to_commit],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if ancestry.returncode:
        raise PriorReleaseInputError("deployed release SHA is not an ancestor of this release")

    artifacts = _get_json(f"{run_url}/artifacts?per_page=100", token).get("artifacts")
    if not isinstance(artifacts, list):
        raise PriorReleaseInputError("Actions artifact response is incomplete")
    expected_name = f"distribution-release-{head_sha}"
    matches = [
        item for item in artifacts
        if item.get("name") == expected_name and item.get("expired") is False
    ]
    if len(matches) != 1 or not isinstance(matches[0].get("archive_download_url"), str):
        raise PriorReleaseInputError(
            f"run must contain exactly one unexpired {expected_name!r} artifact",
        )
    archive = _get_bytes(matches[0]["archive_download_url"], token)
    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        members = [name for name in bundle.namelist() if Path(name).name == "distribution-release.json"]
        if members != ["distribution-release.json"]:
            raise PriorReleaseInputError(
                "release artifact must contain one root distribution-release.json",
            )
        try:
            release = json.loads(bundle.read(members[0]))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise PriorReleaseInputError("release contract is not valid UTF-8 JSON") from error
    release = validate(release, historical=True)
    if release["source"] != {"repository": repository, "commit_sha": head_sha}:
        raise PriorReleaseInputError("release source identity differs from its Actions run")
    if release["build"]["workflow_run_id"] != run_id:
        raise PriorReleaseInputError("release build identity differs from its Actions run")
    return release


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default=os.environ.get("GITHUB_API_URL"))
    parser.add_argument("--repository", required=True)
    parser.add_argument("--run-id", required=True, type=int)
    parser.add_argument("--token-env", default="GITHUB_TOKEN")
    parser.add_argument("--to-commit", required=True)
    parser.add_argument("--output", required=True)
    arguments = parser.parse_args()
    token = os.environ.get(arguments.token_env)
    if not arguments.api_url or not token:
        print("prior release input: GitHub API URL and token are required", file=sys.stderr)
        return 2
    try:
        release = resolve(
            api_url=arguments.api_url.rstrip("/"),
            repository=arguments.repository,
            run_id=arguments.run_id,
            token=token,
            to_commit=arguments.to_commit,
        )
    except (OSError, ValueError, ReleaseArtifactError, zipfile.BadZipFile) as error:
        print(f"prior release input: {error}", file=sys.stderr)
        return 2
    output = Path(arguments.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=output.parent, delete=False,
    ) as stream:
        json.dump(release, stream, indent=2)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, output)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
