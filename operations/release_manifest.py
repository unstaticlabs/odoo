"""Create and validate immutable USL Distribution release manifests."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

from operations.mcp_release import load_release
from operations.module_release import build_inventory, validate_inventory

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "usl-release/v3"
LEGACY_SCHEMA = "usl-release/v2"
COMPONENTS = {"distribution", "backup-tool", "paperless", "sign-dss"}
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


def _sha256(value: object) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _component(value: object, label: str, *, legacy: bool = False) -> dict[str, Any]:
    fields = {"input_sha256", "image", "tag", "digest", "digest_reference"}
    if not legacy:
        fields.add("attestations")
    item = _object(value, fields, label)
    if not SHA256.fullmatch(str(item["input_sha256"])):
        raise ReleaseManifestError(f"{label}.input_sha256 is invalid")
    if not IMAGE.fullmatch(str(item["image"])):
        raise ReleaseManifestError(f"{label}.image is invalid")
    if item["tag"] != f"content-{item['input_sha256']}" or not CONTENT_TAG.fullmatch(str(item["tag"])):
        raise ReleaseManifestError(f"{label}.tag does not match its inputs")
    if not DIGEST.fullmatch(str(item["digest"])):
        raise ReleaseManifestError(f"{label}.digest is invalid")
    if item["digest_reference"] != f"{item['image']}@{item['digest']}":
        raise ReleaseManifestError(f"{label}.digest_reference is invalid")
    if not legacy:
        attestations = _object(item["attestations"], {"sbom", "provenance"}, f"{label}.attestations")
        for name, evidence in attestations.items():
            evidence = _object(evidence, {"predicate_type", "subject_digest"}, f"{label}.{name}")
            if not isinstance(evidence["predicate_type"], str) or not evidence["predicate_type"]:
                raise ReleaseManifestError(f"{label}.{name} predicate is invalid")
            if evidence["subject_digest"] != item["digest"]:
                raise ReleaseManifestError(f"{label}.{name} is not bound to the image")
    return item


def _validate_common(root: dict[str, Any], *, commit: str | None, legacy: bool) -> None:
    source_fields = {"repository", "commit"} if legacy else {"repository", "ref", "commit"}
    source = _object(root["source"], source_fields, "source")
    if not isinstance(source["repository"], str) or "/" not in source["repository"]:
        raise ReleaseManifestError("source.repository must be owner/name")
    if not COMMIT.fullmatch(str(source["commit"])):
        raise ReleaseManifestError("source.commit must be a full Git SHA")
    if not legacy and not re.fullmatch(
        r"refs/heads/(19-usl|19-usl-staging)|refs/tags/recovery-[A-Za-z0-9._-]+",
        str(source["ref"]),
    ):
        raise ReleaseManifestError("source.ref is not a release-authorized ref")
    if commit is not None and source["commit"] != commit:
        raise ReleaseManifestError("release commit differs from the requested commit")
    components = _object(root["components"], COMPONENTS, "components")
    for name in sorted(COMPONENTS):
        _component(components[name], f"components.{name}", legacy=legacy)
    mcp_fields = {"repository", "ref", "commit", "image", "compatibility_sha256"}
    if not legacy:
        mcp_fields |= {"release_schema", "release_manifest_sha256"}
    mcp = _object(root["mcp"], mcp_fields, "mcp")
    if not COMMIT.fullmatch(str(mcp["commit"])) or not IMMUTABLE_IMAGE.fullmatch(str(mcp["image"])):
        raise ReleaseManifestError("MCP identity is not immutable")
    if not SHA256.fullmatch(str(mcp["compatibility_sha256"])):
        raise ReleaseManifestError("MCP compatibility digest is invalid")
    if not legacy:
        if mcp["release_schema"] != "usl-odoo-mcp-oci-release/v2":
            raise ReleaseManifestError("MCP release evidence schema is unsupported")
        if not SHA256.fullmatch(str(mcp["release_manifest_sha256"])):
            raise ReleaseManifestError("MCP release evidence digest is invalid")
    renderer = _object(root["renderer"], {"repository", "commit", "image"}, "renderer")
    if not COMMIT.fullmatch(str(renderer["commit"])) or not IMMUTABLE_IMAGE.fullmatch(str(renderer["image"])):
        raise ReleaseManifestError("renderer identity is not immutable")
    ollama_fields = {"image", "model", "manifest_sha256", "dimension"} if legacy else {"model", "manifest_sha256", "dimension"}
    ollama = _object(root["ollama"], ollama_fields, "ollama")
    if legacy and not IMMUTABLE_IMAGE.fullmatch(str(ollama["image"])):
        raise ReleaseManifestError("Ollama image is not immutable")
    if not isinstance(ollama["model"], str) or not ollama["model"]:
        raise ReleaseManifestError("Ollama model is required")
    if not SHA256.fullmatch(str(ollama["manifest_sha256"])):
        raise ReleaseManifestError("Ollama model manifest is invalid")
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


def validate(payload: object, *, commit: str | None = None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ReleaseManifestError("release must contain an object")
    if payload.get("schema") == LEGACY_SCHEMA:
        root = _object(payload, {"schema", "source", "components", "mcp", "renderer", "ollama", "build"}, "release")
        _validate_common(root, commit=commit, legacy=True)
        return root
    root = _object(
        payload,
        {"schema", "identity", "source", "components", "modules", "foundation", "mcp", "renderer", "ollama", "qualification", "build"},
        "release",
    )
    if root["schema"] != SCHEMA:
        raise ReleaseManifestError(f"unsupported release schema: {root['schema']!r}")
    _validate_common(root, commit=commit, legacy=False)
    validate_inventory(root["modules"])
    foundation = _object(
        root["foundation"],
        {"odoo_series", "odoo_core_commit", "odoo_core_sha256", "oca_sha256", "python_constraints_sha256", "security_policy_sha256", "digest"},
        "foundation",
    )
    if foundation["odoo_series"] != "19.3":
        raise ReleaseManifestError("foundation Odoo series is unsupported")
    if not COMMIT.fullmatch(str(foundation["odoo_core_commit"])):
        raise ReleaseManifestError("foundation Odoo core commit is invalid")
    if not all(
        SHA256.fullmatch(str(foundation[key]))
        for key in ("odoo_core_sha256", "oca_sha256", "python_constraints_sha256", "security_policy_sha256")
    ):
        raise ReleaseManifestError("foundation component digest is invalid")
    if foundation["digest"] != _sha256({key: value for key, value in foundation.items() if key != "digest"}):
        raise ReleaseManifestError("foundation digest differs")
    evidence = _object(root["qualification"], {"evidence"}, "qualification")["evidence"]
    if not isinstance(evidence, dict) or not evidence or not all(
        isinstance(name, str) and name and SHA256.fullmatch(str(digest))
        for name, digest in evidence.items()
    ):
        raise ReleaseManifestError("qualification evidence is incomplete")
    body = {key: value for key, value in root.items() if key != "identity"}
    if root["identity"] != _sha256(body):
        raise ReleaseManifestError("release identity digest differs")
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
        raise ReleaseManifestError("component must be name,input-sha256,image,tag,digest")
    name, input_sha256, image, tag, digest = parts
    if name not in COMPONENTS:
        raise ReleaseManifestError(f"unsupported component: {name}")
    return name, {
        "input_sha256": input_sha256,
        "image": image,
        "tag": tag,
        "digest": digest,
        "digest_reference": f"{image}@{digest}",
        "attestations": {
            "sbom": {"predicate_type": "https://spdx.dev/Document", "subject_digest": digest},
            "provenance": {"predicate_type": "https://slsa.dev/provenance/v1", "subject_digest": digest},
        },
    }


def _parse_evidence(values: list[str]) -> dict[str, str]:
    evidence: dict[str, str] = {}
    for raw in values:
        name, separator, digest = raw.partition("=")
        if not separator or not name or not SHA256.fullmatch(digest) or name in evidence:
            raise ReleaseManifestError("evidence must be unique name=sha256 entries")
        evidence[name] = digest
    return dict(sorted(evidence.items()))


def create(arguments: argparse.Namespace) -> int:
    components = dict(_parse_component(raw) for raw in arguments.component)
    if set(components) != COMPONENTS:
        raise ReleaseManifestError(f"components must be exactly {sorted(COMPONENTS)}")
    mcp = load_release(ROOT)
    renderer = _read_json(Path(arguments.renderer_release), "renderer release")
    if renderer.get("schema") != "usl-external-oci-image/v2":
        raise ReleaseManifestError("renderer release has the wrong schema")
    foundation_body = {
        "odoo_series": arguments.odoo_series,
        "odoo_core_commit": arguments.odoo_core_commit,
        "odoo_core_sha256": arguments.odoo_core_sha256,
        "oca_sha256": arguments.oca_sha256,
        "python_constraints_sha256": arguments.python_constraints_sha256,
        "security_policy_sha256": arguments.security_policy_sha256,
    }
    mcp_release_body = {
        key: mcp[key]
        for key in ("schema", "repository", "ref", "commit", "image_digest", "compatibility_sha256")
    }
    payload: dict[str, Any] = {
        "schema": SCHEMA,
        "source": {"repository": arguments.repository, "ref": arguments.source_ref, "commit": arguments.commit},
        "components": components,
        "modules": build_inventory(ROOT, require_dependencies=True),
        "foundation": {**foundation_body, "digest": _sha256(foundation_body)},
        "mcp": {
            "repository": mcp["repository"],
            "ref": mcp["ref"],
            "commit": mcp["commit"],
            "image": mcp["image"],
            "compatibility_sha256": mcp["compatibility_sha256"],
            "release_schema": "usl-odoo-mcp-oci-release/v2",
            "release_manifest_sha256": _sha256(mcp_release_body),
        },
        "renderer": {"repository": renderer["repository"], "commit": renderer["commit"], "image": renderer["image_digest"]},
        "ollama": {"model": arguments.ollama_model, "manifest_sha256": arguments.ollama_manifest, "dimension": arguments.ollama_dimension},
        "qualification": {"evidence": _parse_evidence(arguments.evidence)},
        "build": {"workflow_run_id": arguments.workflow_run_id, "workflow_run_attempt": arguments.workflow_run_attempt, "workflow_url": arguments.workflow_url},
    }
    payload["identity"] = _sha256(payload)
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
    value = validate(_read_json(path, "release manifest"), commit=arguments.commit)
    print(f"Valid {value['schema']}: {path}")
    return 0


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description=__doc__)
    subcommands = command.add_subparsers(dest="command", required=True)
    create_command = subcommands.add_parser("create")
    create_command.add_argument("--repository", required=True)
    create_command.add_argument("--source-ref", required=True)
    create_command.add_argument("--commit", required=True)
    create_command.add_argument("--component", action="append", required=True)
    create_command.add_argument("--renderer-release", required=True)
    create_command.add_argument("--odoo-core-commit", required=True)
    create_command.add_argument("--odoo-core-sha256", required=True)
    create_command.add_argument("--odoo-series", default="19.3")
    create_command.add_argument("--oca-sha256", required=True)
    create_command.add_argument("--python-constraints-sha256", required=True)
    create_command.add_argument("--security-policy-sha256", required=True)
    create_command.add_argument("--ollama-model", required=True)
    create_command.add_argument("--ollama-manifest", required=True)
    create_command.add_argument("--ollama-dimension", type=int, default=1024)
    create_command.add_argument("--evidence", action="append", default=[], required=True)
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
    except (ReleaseManifestError, ValueError) as error:
        print(f"release manifest: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
