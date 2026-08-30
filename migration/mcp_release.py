"""Immutable source and image identity for the separately maintained Odoo MCP."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any


SCHEMA = "usl-odoo-mcp-release-v1"
COMMIT = re.compile(r"[0-9a-f]{40}\Z")
IMAGE = re.compile(r"[a-z0-9][a-z0-9._/-]*:[A-Za-z0-9_.-]+\Z")
EXPECTED_KEYS = {"schema", "repository", "ref", "commit", "image"}


class McpReleaseError(ValueError):
    """Raised when MCP source or image identity is not exact."""


def _git(repository: Path, *arguments: str) -> str:
    process = subprocess.run(
        ("git", "-C", str(repository), *arguments),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if process.returncode:
        raise McpReleaseError(process.stderr.strip() or "Odoo MCP Git inspection failed")
    return process.stdout.strip()


def _normalized_repository(value: str) -> str:
    normalized = value.strip().removesuffix(".git").removesuffix("/")
    if normalized.startswith("git@github.com:"):
        normalized = "https://github.com/" + normalized.removeprefix("git@github.com:")
    return normalized


def load_release(repository_root: Path) -> dict[str, str]:
    path = repository_root / "deploy/odoo-mcp/release.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise McpReleaseError(f"cannot read Odoo MCP release identity: {path}") from error
    if not isinstance(value, dict) or set(value) != EXPECTED_KEYS:
        raise McpReleaseError("Odoo MCP release identity has unexpected fields")
    if value.get("schema") != SCHEMA:
        raise McpReleaseError("unsupported Odoo MCP release identity schema")
    if not all(isinstance(item, str) and item for item in value.values()):
        raise McpReleaseError("Odoo MCP release identity values must be non-empty strings")
    if not COMMIT.fullmatch(value["commit"]):
        raise McpReleaseError("Odoo MCP release commit must be a full lowercase Git SHA")
    if not IMAGE.fullmatch(value["image"]):
        raise McpReleaseError("Odoo MCP image must use an explicit immutable tag")
    if not value["image"].endswith(value["commit"][:12]):
        raise McpReleaseError("Odoo MCP image tag must end with the release commit prefix")
    return value


def resolve_release(repository_root: Path, source_checkout: Path) -> dict[str, Any]:
    release = load_release(repository_root)
    checkout = source_checkout.expanduser().resolve()
    if not (checkout / ".git").exists():
        raise McpReleaseError(f"Odoo MCP checkout is not a Git repository: {checkout}")
    configured = _normalized_repository(release["repository"])
    actual = _normalized_repository(_git(checkout, "remote", "get-url", "origin"))
    if actual != configured:
        raise McpReleaseError("Odoo MCP checkout origin differs from the pinned repository")
    ref_commit = _git(checkout, "rev-parse", f"{release['ref']}^{{commit}}")
    if ref_commit != release["commit"]:
        raise McpReleaseError("Odoo MCP ref differs from the pinned release commit")
    object_type = _git(checkout, "cat-file", "-t", release["commit"])
    if object_type != "commit":
        raise McpReleaseError("pinned Odoo MCP object is not a Git commit")
    return {**release, "checkout": str(checkout)}
