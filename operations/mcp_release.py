"""Immutable source and image identity for the separately maintained Odoo MCP."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any


SCHEMA = "usl-odoo-mcp-release-v2"
COMPATIBILITY_SCHEMA = "usl-odoo-mcp-compatibility-v1"
COMMIT = re.compile(r"[0-9a-f]{40}\Z")
IMAGE_TAG = re.compile(r"[a-z0-9][a-z0-9._/-]*:[A-Za-z0-9_.-]+\Z")
IMAGE_DIGEST = re.compile(r"[a-z0-9][a-z0-9._/-]*@sha256:[0-9a-f]{64}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
EXPECTED_KEYS = {
    "schema",
    "repository",
    "ref",
    "commit",
    "image_tag",
    "image_digest",
    "compatibility",
    "compatibility_sha256",
}


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


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_compatibility(repository_root: Path, release: dict[str, str]) -> dict[str, Any]:
    relative = Path(release["compatibility"])
    if relative.is_absolute() or ".." in relative.parts:
        raise McpReleaseError("Odoo MCP compatibility path must stay inside the repository")
    path = repository_root / relative
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise McpReleaseError(f"cannot read Odoo MCP compatibility contract: {path}") from error
    if _sha256(path) != release["compatibility_sha256"]:
        raise McpReleaseError("Odoo MCP compatibility contract digest differs from the release")
    expected = {
        "schema",
        "odoo_series",
        "mcp_server_version",
        "required_modules",
        "required_agent_identity",
        "source_rpc_actions",
        "dynamic_rpc_actions",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise McpReleaseError("Odoo MCP compatibility contract has unexpected fields")
    if value.get("schema") != COMPATIBILITY_SCHEMA or value.get("odoo_series") != "19.0":
        raise McpReleaseError("unsupported Odoo MCP compatibility contract")
    for name in ("required_modules", "source_rpc_actions", "dynamic_rpc_actions"):
        items = value.get(name)
        if (
            not isinstance(items, list)
            or items != sorted(set(items))
            or not all(isinstance(item, str) and item for item in items)
        ):
            raise McpReleaseError(f"Odoo MCP compatibility {name} must be sorted and unique")
    identity = value.get("required_agent_identity")
    expected_identity_keys = {
        "method",
        "principal_kind",
        "schema_version",
        "fields",
    }
    if not isinstance(identity, dict) or set(identity) != expected_identity_keys:
        raise McpReleaseError("Odoo MCP Agent identity contract has unexpected fields")
    if (
        identity.get("method") != "usl.agent.current_identity"
        or identity.get("principal_kind") != "agent"
        or not isinstance(identity.get("schema_version"), int)
        or identity["schema_version"] < 1
    ):
        raise McpReleaseError("Odoo MCP Agent identity contract is invalid")
    identity_fields = identity.get("fields")
    if (
        not isinstance(identity_fields, list)
        or identity_fields != sorted(set(identity_fields))
        or not all(isinstance(item, str) and item for item in identity_fields)
        or not {
            "agent",
            "companies",
            "credential",
            "effective_applications",
            "owner",
            "principal_kind",
            "schema_version",
        } <= set(identity_fields)
    ):
        raise McpReleaseError("Odoo MCP Agent identity fields are invalid")
    return value


def load_release(repository_root: Path) -> dict[str, Any]:
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
    if not COMMIT.fullmatch(value["ref"]) or value["ref"] != value["commit"]:
        raise McpReleaseError("Odoo MCP release ref must equal the full pinned commit")
    if not IMAGE_TAG.fullmatch(value["image_tag"]):
        raise McpReleaseError("Odoo MCP build image must use an explicit commit tag")
    tag = value["image_tag"].rsplit(":", 1)[1]
    if tag not in {value["commit"][:12], f"sha-{value['commit']}"}:
        raise McpReleaseError("Odoo MCP image tag must identify the release commit")
    if not IMAGE_DIGEST.fullmatch(value["image_digest"]):
        raise McpReleaseError("Odoo MCP runtime image must use an immutable digest")
    if not SHA256.fullmatch(value["compatibility_sha256"]):
        raise McpReleaseError("Odoo MCP compatibility digest is invalid")
    compatibility = _load_compatibility(repository_root, value)
    return {**value, "image": value["image_digest"], "contract": compatibility}


def _source_contract(checkout: Path, commit: str) -> tuple[str, list[str], list[str]]:
    version_source = _git(checkout, "show", f"{commit}:src/version.ts")
    version_match = re.search(r'SERVER_VERSION\s*=\s*"([^"]+)"', version_source)
    if not version_match:
        raise McpReleaseError("Odoo MCP source has no public server version")
    modules: set[str] = set()
    actions: set[str] = set()
    call_start = re.compile(r"client\.call")
    exact_call = re.compile(
        r'\(\s*context\s*,\s*"([^"]+)"\s*,\s*"([^"]+)"',
    )
    source_paths = sorted(
        path
        for path in _git(checkout, "ls-tree", "-r", "--name-only", commit, "src").splitlines()
        if path.endswith(".ts")
    )
    for path in source_paths:
        source = _git(checkout, "show", f"{commit}:{path}")
        for raw in re.findall(r"requiredModules:\s*\[([^\]]*)\]", source):
            modules.update(re.findall(r'"([^"]+)"', raw))
        offset = 0
        while match := call_start.search(source, offset):
            exact = exact_call.search(source, match.start(), match.start() + 5000)
            offset = match.end()
            if exact:
                actions.add(f"rpc:{exact.group(1)}.{exact.group(2)}")
                offset = exact.end()
    return version_match.group(1), sorted(modules), sorted(actions)


def verify_compatibility(
    repository_root: Path,
    checkout: Path,
    commit: str,
    contract: dict[str, Any],
) -> dict[str, Any]:
    version, modules, source_actions = _source_contract(checkout, commit)
    if version != contract["mcp_server_version"]:
        raise McpReleaseError("Odoo MCP server version differs from its compatibility contract")
    if modules != contract["required_modules"]:
        raise McpReleaseError("Odoo MCP module requirements differ from its compatibility contract")
    if source_actions != contract["source_rpc_actions"]:
        raise McpReleaseError("Odoo MCP source RPC surface differs from its compatibility contract")
    surface_path = repository_root / "custom-addons/usl_access_control/policy/action_surface.json"
    try:
        surface = json.loads(surface_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise McpReleaseError("cannot read the qualified Odoo action surface") from error
    installed = {item.get("name") for item in surface.get("modules", []) if isinstance(item, dict)}
    missing_modules = sorted(set(contract["required_modules"]) - installed)
    available_actions = {
        item.get("key") for item in surface.get("actions", []) if isinstance(item, dict)
    }
    identity_action = f"rpc:{contract['required_agent_identity']['method']}"
    required_actions = (
        set(contract["source_rpc_actions"])
        | set(contract["dynamic_rpc_actions"])
        | {identity_action}
    )
    missing_actions = sorted(required_actions - available_actions)
    if missing_modules or missing_actions:
        details = []
        if missing_modules:
            details.append("modules=" + ",".join(missing_modules))
        if missing_actions:
            details.append("actions=" + ",".join(missing_actions))
        raise McpReleaseError("Odoo–MCP compatibility contract is not satisfied: " + "; ".join(details))
    return {
        "schema": COMPATIBILITY_SCHEMA,
        "status": "passed",
        "odoo_series": contract["odoo_series"],
        "mcp_server_version": version,
        "required_module_count": len(modules),
        "required_rpc_action_count": len(required_actions),
    }


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
    compatibility = verify_compatibility(
        repository_root,
        checkout,
        release["commit"],
        release["contract"],
    )
    return {**release, "checkout": str(checkout), "compatibility_result": compatibility}
