#!/usr/bin/env python3
"""Create and validate the immutable Distribution release artifact."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


SCHEMA = "usl-distribution-release/v4"
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


def _validate_image(value: object, *, context: str, commit: str) -> dict[str, Any]:
    image_value = _exact_keys(value, {"name", "tag", "digest", "digest_reference"}, context)
    if not isinstance(image_value["name"], str) or not IMAGE.fullmatch(image_value["name"]):
        raise ReleaseArtifactError(f"{context}.name must be a lowercase ghcr.io reference")
    expected_tag = f"sha-{commit}"
    if image_value["tag"] != expected_tag:
        raise ReleaseArtifactError(f"{context}.tag must be {expected_tag!r}")
    if not isinstance(image_value["digest"], str) or not DIGEST.fullmatch(image_value["digest"]):
        raise ReleaseArtifactError(f"{context}.digest must be a lowercase sha256 digest")
    expected_reference = f"{image_value['name']}@{image_value['digest']}"
    if image_value["digest_reference"] != expected_reference:
        raise ReleaseArtifactError(f"{context}.digest_reference must be {expected_reference!r}")
    return image_value


def validate(
    payload: object,
    *,
    commit: str | None = None,
    image: str | None = None,
    backup_tool_image: str | None = None,
    paperless_image: str | None = None,
    document_renderer_image: str | None = None,
    sign_dss_image: str | None = None,
    mcp_commit: str | None = None,
    mcp_image: str | None = None,
) -> dict[str, Any]:
    root = _exact_keys(
        payload,
        {
            "schema",
            "source",
            "image",
            "backup_tool",
            "paperless",
            "document_renderer",
            "sign_dss",
            "mcp",
            "build",
            "attestations",
        },
        "artifact",
    )
    if root["schema"] != SCHEMA:
        raise ReleaseArtifactError(f"unsupported schema: {root['schema']!r}")

    source = _exact_keys(root["source"], {"repository", "commit_sha"}, "source")
    if not isinstance(source["repository"], str) or not REPOSITORY.fullmatch(source["repository"]):
        raise ReleaseArtifactError("source.repository must be owner/name")
    if not isinstance(source["commit_sha"], str) or not COMMIT.fullmatch(source["commit_sha"]):
        raise ReleaseArtifactError("source.commit_sha must be a full lowercase Git SHA")

    image_value = _validate_image(root["image"], context="image", commit=source["commit_sha"])
    backup_tool = _validate_image(
        root["backup_tool"], context="backup_tool", commit=source["commit_sha"]
    )
    paperless = _validate_image(
        root["paperless"], context="paperless", commit=source["commit_sha"]
    )
    sign_dss = _validate_image(
        root["sign_dss"], context="sign_dss", commit=source["commit_sha"]
    )
    document_renderer = _exact_keys(
        root["document_renderer"], {"repository", "commit", "image"}, "document_renderer"
    )
    if not isinstance(document_renderer["repository"], str) or not document_renderer[
        "repository"
    ].startswith("https://github.com/"):
        raise ReleaseArtifactError(
            "document_renderer.repository must be an HTTPS GitHub repository"
        )
    if not isinstance(document_renderer["commit"], str) or not COMMIT.fullmatch(
        document_renderer["commit"]
    ):
        raise ReleaseArtifactError("document_renderer.commit must be a full Git SHA")
    renderer_image = _validate_image(
        document_renderer["image"],
        context="document_renderer.image",
        commit=document_renderer["commit"],
    )
    mcp = _exact_keys(
        root["mcp"],
        {
            "repository",
            "ref",
            "commit",
            "image_digest",
            "compatibility_sha256",
        },
        "mcp",
    )
    if not isinstance(mcp["repository"], str) or not mcp["repository"].startswith(
        "https://github.com/"
    ):
        raise ReleaseArtifactError("mcp.repository must be an HTTPS GitHub repository")
    if not isinstance(mcp["ref"], str) or not mcp["ref"]:
        raise ReleaseArtifactError("mcp.ref must be explicit")
    if not isinstance(mcp["commit"], str) or not COMMIT.fullmatch(mcp["commit"]):
        raise ReleaseArtifactError("mcp.commit must be a full lowercase Git SHA")
    if not isinstance(mcp["image_digest"], str) or not re.fullmatch(
        r"[a-z0-9][a-z0-9._/-]*@sha256:[0-9a-f]{64}", mcp["image_digest"]
    ):
        raise ReleaseArtifactError("mcp.image_digest must be immutable")
    if not isinstance(mcp["compatibility_sha256"], str) or not DIGEST.fullmatch(
        "sha256:" + mcp["compatibility_sha256"]
    ):
        raise ReleaseArtifactError("mcp.compatibility_sha256 must be a lowercase SHA-256")

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

    attested_images = {
        "distribution",
        "backup_tool",
        "paperless",
        "document_renderer",
        "sign_dss",
    }
    attestations = _exact_keys(root["attestations"], attested_images, "attestations")
    for artifact_name in sorted(attested_images):
        artifact_attestations = _exact_keys(
            attestations[artifact_name],
            {"oci_sbom", "buildkit_provenance", "github_provenance"},
            f"attestations.{artifact_name}",
        )
        for key in ("oci_sbom", "buildkit_provenance", "github_provenance"):
            if artifact_attestations[key] != "generated":
                raise ReleaseArtifactError(
                    f"attestations.{artifact_name}.{key} must be 'generated'"
                )

    if commit is not None and source["commit_sha"] != commit:
        raise ReleaseArtifactError("artifact commit does not match the requested commit")
    if image is not None and image_value["name"] != image:
        raise ReleaseArtifactError("artifact image does not match the requested image")
    if backup_tool_image is not None and backup_tool["name"] != backup_tool_image:
        raise ReleaseArtifactError("artifact backup tool image does not match the requested image")
    if paperless_image is not None and paperless["name"] != paperless_image:
        raise ReleaseArtifactError("artifact Paperless image does not match the requested image")
    if document_renderer_image is not None and renderer_image["name"] != document_renderer_image:
        raise ReleaseArtifactError(
            "artifact document renderer image does not match the requested image"
        )
    if sign_dss_image is not None and sign_dss["name"] != sign_dss_image:
        raise ReleaseArtifactError("artifact Sign DSS image does not match the requested image")
    if mcp_commit is not None and mcp["commit"] != mcp_commit:
        raise ReleaseArtifactError("artifact MCP commit does not match the requested commit")
    if mcp_image is not None and mcp["image_digest"] != mcp_image:
        raise ReleaseArtifactError("artifact MCP image does not match the requested digest")
    return root


def create(arguments: argparse.Namespace) -> int:
    from migration.mcp_release import load_release

    mcp_release = load_release(Path(__file__).resolve().parents[1])
    payload = {
        "schema": SCHEMA,
        "source": {"repository": arguments.repository, "commit_sha": arguments.commit},
        "image": {
            "name": arguments.image,
            "tag": arguments.tag,
            "digest": arguments.digest,
            "digest_reference": f"{arguments.image}@{arguments.digest}",
        },
        "backup_tool": {
            "name": arguments.backup_tool_image,
            "tag": arguments.tag,
            "digest": arguments.backup_tool_digest,
            "digest_reference": f"{arguments.backup_tool_image}@{arguments.backup_tool_digest}",
        },
        "paperless": {
            "name": arguments.paperless_image,
            "tag": arguments.tag,
            "digest": arguments.paperless_digest,
            "digest_reference": f"{arguments.paperless_image}@{arguments.paperless_digest}",
        },
        "document_renderer": {
            "repository": arguments.document_renderer_repository,
            "commit": arguments.document_renderer_commit,
            "image": {
                "name": arguments.document_renderer_image,
                "tag": f"sha-{arguments.document_renderer_commit}",
                "digest": arguments.document_renderer_digest,
                "digest_reference": (
                    f"{arguments.document_renderer_image}@{arguments.document_renderer_digest}"
                ),
            },
        },
        "sign_dss": {
            "name": arguments.sign_dss_image,
            "tag": arguments.tag,
            "digest": arguments.sign_dss_digest,
            "digest_reference": f"{arguments.sign_dss_image}@{arguments.sign_dss_digest}",
        },
        "mcp": {
            "repository": mcp_release["repository"],
            "ref": mcp_release["ref"],
            "commit": mcp_release["commit"],
            "image_digest": mcp_release["image"],
            "compatibility_sha256": mcp_release["compatibility_sha256"],
        },
        "build": {
            "workflow_run_id": arguments.workflow_run_id,
            "workflow_run_attempt": arguments.workflow_run_attempt,
            "workflow_url": arguments.workflow_url,
        },
        "attestations": {
            name: {
                "oci_sbom": "generated",
                "buildkit_provenance": "generated",
                "github_provenance": "generated",
            }
            for name in (
                "distribution",
                "backup_tool",
                "paperless",
                "document_renderer",
                "sign_dss",
            )
        },
    }
    validate(
        payload,
        commit=arguments.commit,
        image=arguments.image,
        backup_tool_image=arguments.backup_tool_image,
        paperless_image=arguments.paperless_image,
        document_renderer_image=arguments.document_renderer_image,
        sign_dss_image=arguments.sign_dss_image,
        mcp_commit=mcp_release["commit"],
        mcp_image=mcp_release["image"],
    )
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
    validate(
        payload,
        commit=arguments.commit,
        image=arguments.image,
        backup_tool_image=arguments.backup_tool_image,
        paperless_image=arguments.paperless_image,
        document_renderer_image=arguments.document_renderer_image,
        sign_dss_image=arguments.sign_dss_image,
        mcp_commit=arguments.mcp_commit,
        mcp_image=arguments.mcp_image,
    )
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
    create_command.add_argument("--backup-tool-image", required=True)
    create_command.add_argument("--backup-tool-digest", required=True)
    create_command.add_argument("--paperless-image", required=True)
    create_command.add_argument("--paperless-digest", required=True)
    create_command.add_argument("--document-renderer-repository", required=True)
    create_command.add_argument("--document-renderer-commit", required=True)
    create_command.add_argument("--document-renderer-image", required=True)
    create_command.add_argument("--document-renderer-digest", required=True)
    create_command.add_argument("--sign-dss-image", required=True)
    create_command.add_argument("--sign-dss-digest", required=True)
    create_command.add_argument("--workflow-run-id", required=True, type=int)
    create_command.add_argument("--workflow-run-attempt", required=True, type=int)
    create_command.add_argument("--workflow-url", required=True)
    create_command.add_argument("--output", required=True)
    create_command.set_defaults(handler=create)
    validate_command = subcommands.add_parser("validate")
    validate_command.add_argument("path")
    validate_command.add_argument("--commit")
    validate_command.add_argument("--image")
    validate_command.add_argument("--backup-tool-image")
    validate_command.add_argument("--paperless-image")
    validate_command.add_argument("--document-renderer-image")
    validate_command.add_argument("--sign-dss-image")
    validate_command.add_argument("--mcp-commit")
    validate_command.add_argument("--mcp-image")
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
