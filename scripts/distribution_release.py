#!/usr/bin/env python3
"""Create and validate the immutable Distribution image handoff artifact."""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any


SCHEMA = "usl-distribution-release/v1"
COMMIT = re.compile(r"[0-9a-f]{40}")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
IMAGE = re.compile(r"ghcr\.io/[a-z0-9][a-z0-9._/-]*")
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


class ReleaseArtifactError(ValueError):
    """The release artifact is malformed or refers to another build."""


def _exact_keys(value: object, expected: set[str], context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReleaseArtifactError(f"{context} must be an object")
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ReleaseArtifactError(f"{context} keys differ (missing={missing}, extra={extra})")
    return value


def validate(payload: object, *, commit: str | None = None, image: str | None = None) -> dict[str, Any]:
    root = _exact_keys(payload, {"schema", "source", "image", "build", "attestations"}, "artifact")
    if root["schema"] != SCHEMA:
        raise ReleaseArtifactError(f"unsupported schema: {root['schema']!r}")

    source = _exact_keys(root["source"], {"repository", "commit_sha"}, "source")
    if not isinstance(source["repository"], str) or not REPOSITORY.fullmatch(source["repository"]):
        raise ReleaseArtifactError("source.repository must be owner/name")
    if not isinstance(source["commit_sha"], str) or not COMMIT.fullmatch(source["commit_sha"]):
        raise ReleaseArtifactError("source.commit_sha must be a full lowercase Git SHA")

    image_value = _exact_keys(root["image"], {"name", "tag", "digest", "digest_reference"}, "image")
    if not isinstance(image_value["name"], str) or not IMAGE.fullmatch(image_value["name"]):
        raise ReleaseArtifactError("image.name must be a lowercase ghcr.io reference")
    expected_tag = f"sha-{source['commit_sha']}"
    if image_value["tag"] != expected_tag:
        raise ReleaseArtifactError(f"image.tag must be {expected_tag!r}")
    if not isinstance(image_value["digest"], str) or not DIGEST.fullmatch(image_value["digest"]):
        raise ReleaseArtifactError("image.digest must be a lowercase sha256 digest")
    expected_reference = f"{image_value['name']}@{image_value['digest']}"
    if image_value["digest_reference"] != expected_reference:
        raise ReleaseArtifactError(f"image.digest_reference must be {expected_reference!r}")

    build = _exact_keys(root["build"], {"workflow_run_id", "workflow_run_attempt", "workflow_url"}, "build")
    if not isinstance(build["workflow_run_id"], int) or build["workflow_run_id"] < 1:
        raise ReleaseArtifactError("build.workflow_run_id must be a positive integer")
    if not isinstance(build["workflow_run_attempt"], int) or build["workflow_run_attempt"] < 1:
        raise ReleaseArtifactError("build.workflow_run_attempt must be a positive integer")
    expected_url = (
        f"https://github.com/{source['repository']}/actions/runs/{build['workflow_run_id']}"
    )
    if build["workflow_url"] != expected_url:
        raise ReleaseArtifactError(f"build.workflow_url must be {expected_url!r}")

    attestations = _exact_keys(
        root["attestations"], {"oci_sbom", "buildkit_provenance", "github_provenance"}, "attestations"
    )
    for key in ("oci_sbom", "buildkit_provenance", "github_provenance"):
        if attestations[key] != "generated":
            raise ReleaseArtifactError(f"attestations.{key} must be 'generated'")

    if commit is not None and source["commit_sha"] != commit:
        raise ReleaseArtifactError("artifact commit does not match the requested commit")
    if image is not None and image_value["name"] != image:
        raise ReleaseArtifactError("artifact image does not match the requested image")
    return root


def create(arguments: argparse.Namespace) -> int:
    payload = {
        "schema": SCHEMA,
        "source": {"repository": arguments.repository, "commit_sha": arguments.commit},
        "image": {
            "name": arguments.image,
            "tag": arguments.tag,
            "digest": arguments.digest,
            "digest_reference": f"{arguments.image}@{arguments.digest}",
        },
        "build": {
            "workflow_run_id": arguments.workflow_run_id,
            "workflow_run_attempt": arguments.workflow_run_attempt,
            "workflow_url": arguments.workflow_url,
        },
        "attestations": {
            "oci_sbom": "generated",
            "buildkit_provenance": "generated",
            "github_provenance": "generated",
        },
    }
    validate(payload, commit=arguments.commit, image=arguments.image)
    output = Path(arguments.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=output.parent, delete=False) as stream:
        json.dump(payload, stream, indent=2)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, output)
    print(output)
    return 0


def validate_file(arguments: argparse.Namespace) -> int:
    path = Path(arguments.path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReleaseArtifactError(f"cannot read {path}: {error}") from error
    validate(payload, commit=arguments.commit, image=arguments.image)
    print(f"Valid {SCHEMA}: {path}")
    return 0


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description=__doc__)
    subcommands = command.add_subparsers(dest="command", required=True)
    create_command = subcommands.add_parser("create")
    create_command.add_argument("--repository", required=True)
    create_command.add_argument("--commit", required=True)
    create_command.add_argument("--image", required=True)
    create_command.add_argument("--tag", required=True)
    create_command.add_argument("--digest", required=True)
    create_command.add_argument("--workflow-run-id", required=True, type=int)
    create_command.add_argument("--workflow-run-attempt", required=True, type=int)
    create_command.add_argument("--workflow-url", required=True)
    create_command.add_argument("--output", required=True)
    create_command.set_defaults(handler=create)
    validate_command = subcommands.add_parser("validate")
    validate_command.add_argument("path")
    validate_command.add_argument("--commit")
    validate_command.add_argument("--image")
    validate_command.set_defaults(handler=validate_file)
    return command


def main() -> int:
    arguments = parser().parse_args()
    try:
        return arguments.handler(arguments)
    except ReleaseArtifactError as error:
        print(f"distribution release: {error}", file=os.sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
