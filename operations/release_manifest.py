"""Create and validate a content-addressed USL Distribution release manifest."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

from operations.mcp_release import load_release


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "usl-release/v2"
COMPONENTS = {
    "backup-tool",
    "distribution",
    "paperless",
    "receipt-egress",
    "receipt-fetcher",
    "sign-dss",
}
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
COMMIT = re.compile(r"[0-9a-f]{40}\Z")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
IMAGE = re.compile(r"ghcr\.io/[a-z0-9][a-z0-9._/-]*\Z")
CONTENT_TAG = re.compile(r"content-[0-9a-f]{64}\Z")
IMMUTABLE_IMAGE = re.compile(r"[a-z0-9][a-z0-9._/-]*@sha256:[0-9a-f]{64}\Z")


class ReleaseManifestError(ValueError):
    """The release manifest is malformed or mutable."""


def _object(value: object, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        actual = sorted(value) if isinstance(value, dict) else type(value).__name__
        raise ReleaseManifestError(f"{label} fields differ: {actual}")
    return value


def _component(value: object, label: str) -> dict[str, Any]:
    item = _object(
        value,
        {"input_sha256", "image", "tag", "digest", "digest_reference"},
        label,
    )
    if not SHA256.fullmatch(str(item["input_sha256"])):
        raise ReleaseManifestError(f"{label}.input_sha256 is invalid")
    if not IMAGE.fullmatch(str(item["image"])):
        raise ReleaseManifestError(f"{label}.image is invalid")
    if item["tag"] != f"content-{item['input_sha256']}" or not CONTENT_TAG.fullmatch(
        str(item["tag"]),
    ):
        raise ReleaseManifestError(f"{label}.tag does not match its inputs")
    if not DIGEST.fullmatch(str(item["digest"])):
        raise ReleaseManifestError(f"{label}.digest is invalid")
    expected_reference = f"{item['image']}@{item['digest']}"
    if item["digest_reference"] != expected_reference:
        raise ReleaseManifestError(f"{label}.digest_reference is invalid")
    return item


def validate(payload: object, *, commit: str | None = None) -> dict[str, Any]:
    root = _object(
        payload,
        {"schema", "source", "components", "mcp", "renderer", "ollama", "build"},
        "release",
    )
    if root["schema"] != SCHEMA:
        raise ReleaseManifestError(f"unsupported release schema: {root['schema']!r}")
    source = _object(root["source"], {"repository", "commit"}, "source")
    if not isinstance(source["repository"], str) or "/" not in source["repository"]:
        raise ReleaseManifestError("source.repository must be owner/name")
    if not COMMIT.fullmatch(str(source["commit"])):
        raise ReleaseManifestError("source.commit must be a full Git SHA")
    if commit is not None and source["commit"] != commit:
        raise ReleaseManifestError("release commit differs from the requested commit")

    components = _object(root["components"], COMPONENTS, "components")
    for name in sorted(COMPONENTS):
        _component(components[name], f"components.{name}")

    mcp = _object(
        root["mcp"],
        {"repository", "ref", "commit", "image", "compatibility_sha256"},
        "mcp",
    )
    if not COMMIT.fullmatch(str(mcp["commit"])) or not IMMUTABLE_IMAGE.fullmatch(
        str(mcp["image"]),
    ):
        raise ReleaseManifestError("MCP identity is not immutable")
    if not SHA256.fullmatch(str(mcp["compatibility_sha256"])):
        raise ReleaseManifestError("MCP compatibility digest is invalid")

    renderer = _object(root["renderer"], {"repository", "commit", "image"}, "renderer")
    if not COMMIT.fullmatch(str(renderer["commit"])) or not IMMUTABLE_IMAGE.fullmatch(
        str(renderer["image"]),
    ):
        raise ReleaseManifestError("renderer identity is not immutable")

    ollama = _object(
        root["ollama"], {"image", "model", "manifest_sha256", "dimension"}, "ollama"
    )
    if not IMMUTABLE_IMAGE.fullmatch(str(ollama["image"])):
        raise ReleaseManifestError("Ollama image is not immutable")
    if not isinstance(ollama["model"], str) or not ollama["model"]:
        raise ReleaseManifestError("Ollama model is required")
    if not SHA256.fullmatch(str(ollama["manifest_sha256"])):
        raise ReleaseManifestError("Ollama manifest digest is invalid")
    if ollama["dimension"] != 1024:
        raise ReleaseManifestError("Ollama embedding dimension must be 1024")

    build = _object(root["build"], {"workflow_run_id", "workflow_run_attempt", "workflow_url"}, "build")
    if not isinstance(build["workflow_run_id"], int) or build["workflow_run_id"] < 1:
        raise ReleaseManifestError("workflow run ID must be positive")
    if not isinstance(build["workflow_run_attempt"], int) or build["workflow_run_attempt"] < 1:
        raise ReleaseManifestError("workflow run attempt must be positive")
    expected_url = f"https://github.com/{source['repository']}/actions/runs/{build['workflow_run_id']}"
    if build["workflow_url"] != expected_url:
        raise ReleaseManifestError("workflow URL does not match the release source")
    return root


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReleaseManifestError(f"cannot read {label}: {path}") from error
    if not isinstance(value, dict):
        raise ReleaseManifestError(f"{label} must contain an object")
    return value


def _parse_component(raw: str) -> tuple[str, dict[str, Any]]:
    parts = raw.split(",")
    if len(parts) != 5:
        raise ReleaseManifestError(
            "component must be name,input-sha256,image,tag,digest",
        )
    name, input_sha256, image, tag, digest = parts
    if name not in COMPONENTS:
        raise ReleaseManifestError(f"unsupported component: {name}")
    return name, {
        "input_sha256": input_sha256,
        "image": image,
        "tag": tag,
        "digest": digest,
        "digest_reference": f"{image}@{digest}",
    }


def create(arguments: argparse.Namespace) -> int:
    components = dict(_parse_component(raw) for raw in arguments.component)
    if set(components) != COMPONENTS:
        raise ReleaseManifestError(f"components must be exactly {sorted(COMPONENTS)}")
    mcp = load_release(ROOT)
    renderer = _read_json(Path(arguments.renderer_release), "renderer release")
    if renderer.get("schema") != "usl-external-oci-image/v2":
        raise ReleaseManifestError("renderer release has the wrong schema")
    payload = {
        "schema": SCHEMA,
        "source": {"repository": arguments.repository, "commit": arguments.commit},
        "components": components,
        "mcp": {
            "repository": mcp["repository"],
            "ref": mcp["ref"],
            "commit": mcp["commit"],
            "image": mcp["image"],
            "compatibility_sha256": mcp["compatibility_sha256"],
        },
        "renderer": {
            "repository": renderer["repository"],
            "commit": renderer["commit"],
            "image": renderer["image_digest"],
        },
        "ollama": {
            "image": arguments.ollama_image,
            "model": arguments.ollama_model,
            "manifest_sha256": arguments.ollama_manifest,
            "dimension": arguments.ollama_dimension,
        },
        "build": {
            "workflow_run_id": arguments.workflow_run_id,
            "workflow_run_attempt": arguments.workflow_run_attempt,
            "workflow_url": arguments.workflow_url,
        },
    }
    validate(payload, commit=arguments.commit)
    output = Path(arguments.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=output.parent, delete=False) as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, output)
    print(output)
    return 0


def validate_file(arguments: argparse.Namespace) -> int:
    path = Path(arguments.path)
    validate(_read_json(path, "release manifest"), commit=arguments.commit)
    print(f"Valid {SCHEMA}: {path}")
    return 0


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description=__doc__)
    subcommands = command.add_subparsers(dest="command", required=True)
    create_command = subcommands.add_parser("create")
    create_command.add_argument("--repository", required=True)
    create_command.add_argument("--commit", required=True)
    create_command.add_argument("--component", action="append", required=True)
    create_command.add_argument("--renderer-release", required=True)
    create_command.add_argument("--ollama-image", required=True)
    create_command.add_argument("--ollama-model", required=True)
    create_command.add_argument("--ollama-manifest", required=True)
    create_command.add_argument("--ollama-dimension", type=int, default=1024)
    create_command.add_argument("--workflow-run-id", type=int, required=True)
    create_command.add_argument("--workflow-run-attempt", type=int, required=True)
    create_command.add_argument("--workflow-url", required=True)
    create_command.add_argument("--output", required=True)
    create_command.set_defaults(handler=create)
    validate_command = subcommands.add_parser("validate")
    validate_command.add_argument("path")
    validate_command.add_argument("--commit")
    validate_command.set_defaults(handler=validate_file)
    return command


def main() -> int:
    arguments = parser().parse_args()
    try:
        return arguments.handler(arguments)
    except ReleaseManifestError as error:
        print(f"release manifest: {error}", file=sys.stderr)
        return 2
