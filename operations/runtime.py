"""Validated runtime targets and exact Docker Compose ownership checks."""

from __future__ import annotations

import json
import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


SCHEMA = "usl-runtime/v1"
TARGET_NAME = re.compile(r"[a-z][a-z0-9-]{1,31}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
SECRET_KEY = re.compile(r"[A-Z][A-Z0-9_]*\Z")
RESERVED_SECRET_KEYS = {
    "COMPOSE_FILE",
    "COMPOSE_PROJECT_NAME",
    "ODOO_DB_NAME",
    "ODOO_DB_HOST",
    "ODOO_DB_PORT",
    "ODOO_HTTP_PORT",
    "ODOO_GEVENT_PORT",
    "ODOO_IMAGE",
    "ODOO_PUBLIC_BASE_URL",
    "ODOO_MCP_IMAGE",
    "ODOO_MCP_PUBLIC_ORIGIN",
    "PAPERLESS_DB_NAME",
    "PAPERLESS_DB_HOST",
    "PAPERLESS_DB_PORT",
    "PAPERLESS_HTTP_PORT",
    "PAPERLESS_IMAGE",
    "PAPERLESS_PUBLIC_URL",
    "RESTIC_REPOSITORY",
    "USL_BACKUP_CACHE_REPOSITORY",
    "USL_EXTERNAL_INGRESS_NETWORK",
    "USL_EXTERNAL_OLLAMA_NETWORK",
}
RESERVED_SECRET_SUFFIXES = ("_IMAGE", "_NETWORK", "_PORT", "_URL", "_VOLUME")


class RuntimeError(RuntimeError):
    """A target, secret binding, or Docker resource is unsafe."""


class Runner(Protocol):
    def run(self, command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]: ...


@dataclass
class CommandRunner:
    prefix: tuple[str, ...] = ()

    def run(
        self,
        command: list[str],
        *,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        invocation = [*self.prefix, *command]
        if self.prefix and self.prefix[0] == "ssh":
            invocation = [self.prefix[0], self.prefix[1], "--", shlex.join(command)]
        process = subprocess.run(
            invocation,
            check=False,
            capture_output=True,
            text=True,
        )
        if check and process.returncode:
            detail = (process.stderr or process.stdout).strip()
            raise RuntimeError(f"command failed ({' '.join(command)}): {detail}")
        return process


def _exact(value: object, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        actual = sorted(value) if isinstance(value, dict) else type(value).__name__
        raise RuntimeError(f"{label} fields differ: {actual}")
    return value


def _strings(value: object, label: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise RuntimeError(f"{label} must be a non-empty string list")
    return value


@dataclass(frozen=True)
class Target:
    path: Path
    value: dict[str, Any]

    @property
    def name(self) -> str:
        return self.value["name"]

    @property
    def project(self) -> str:
        return self.value["compose"]["project"]

    @property
    def protected(self) -> bool:
        return self.value["protected"]

    def runner(self) -> CommandRunner:
        transport = self.value["transport"]
        if transport["type"] == "local":
            return CommandRunner()
        return CommandRunner(("ssh", transport["host"], "--"))


def validate_target(payload: object, path: Path = Path("<memory>")) -> Target:
    root = _exact(
        payload,
        {
            "schema",
            "name",
            "environment",
            "protected",
            "transport",
            "compose",
            "services",
            "databases",
            "volumes",
            "paths",
            "external_networks",
            "endpoints",
            "ingress",
            "ollama",
            "backup",
            "release_manifest",
            "secrets",
            "state_directory",
        },
        "target",
    )
    if root["schema"] != SCHEMA:
        raise RuntimeError(f"unsupported target schema: {root['schema']!r}")
    if not TARGET_NAME.fullmatch(str(root["name"])):
        raise RuntimeError("target.name is invalid")
    if root["environment"] not in {"production", "staging", "local"}:
        raise RuntimeError("target.environment is invalid")
    if not isinstance(root["protected"], bool):
        raise RuntimeError("target.protected must be boolean")

    transport = root["transport"]
    if not isinstance(transport, dict) or transport.get("type") not in {"local", "ssh"}:
        raise RuntimeError("transport.type must be local or ssh")
    expected_transport = {"type"} if transport.get("type") == "local" else {"type", "host"}
    _exact(transport, expected_transport, "transport")
    if transport.get("type") == "ssh" and not TARGET_NAME.fullmatch(str(transport.get("host"))):
        raise RuntimeError("transport.host is invalid")

    compose = _exact(
        root["compose"],
        {"project", "anchor_service", "profiles", "default_network", "resource_overlay"},
        "compose",
    )
    if not TARGET_NAME.fullmatch(str(compose["project"])):
        raise RuntimeError("compose.project is invalid")
    if not isinstance(compose["anchor_service"], str) or not compose["anchor_service"]:
        raise RuntimeError("compose.anchor_service is required")
    if not isinstance(compose["profiles"], list) or not all(
        isinstance(item, str) and item for item in compose["profiles"]
    ):
        raise RuntimeError("compose.profiles must be a string list")
    if not isinstance(compose["default_network"], str) or not compose["default_network"]:
        raise RuntimeError("compose.default_network is required")
    resource_overlay = compose["resource_overlay"]
    if resource_overlay is not None and (
        not isinstance(resource_overlay, str)
        or resource_overlay.startswith("/")
        or ".." in Path(resource_overlay).parts
        or not resource_overlay.endswith(".json")
    ):
        raise RuntimeError("compose.resource_overlay must be a safe relative JSON path")

    services = root["services"]
    required_services = {
        "odoo",
        "odoo_db",
        "paperless",
        "paperless_db",
        "paperless_broker",
        "paperless_gotenberg",
        "paperless_tika",
        "mcp",
        "renderer",
        "sign",
        "sign_ca",
    }
    _exact(services, required_services, "services")
    if not all(isinstance(value, str) and value for value in services.values()):
        raise RuntimeError("service names must be non-empty strings")

    databases = _exact(root["databases"], {"odoo", "paperless"}, "databases")
    for name, database in databases.items():
        _exact(database, {"service", "name", "user", "password_key"}, f"databases.{name}")
        if database["service"] not in services.values():
            raise RuntimeError(f"databases.{name}.service is not declared")
        for field in ("name", "user", "password_key"):
            if not isinstance(database[field], str) or not database[field]:
                raise RuntimeError(f"databases.{name}.{field} is required")
        if not SECRET_KEY.fullmatch(database["password_key"]):
            raise RuntimeError(f"databases.{name}.password_key is invalid")

    volumes = root["volumes"]
    required_volumes = {
        "odoo_filestore",
        "paperless_media",
        "paperless_data",
        "paperless_trash",
        "paperless_consume",
        "odoo_postgres",
        "paperless_postgres",
        "paperless_broker",
        "paperless_export",
        "mcp_oauth",
    }
    if not isinstance(volumes, dict) or not required_volumes <= set(volumes):
        raise RuntimeError(f"target volumes must include {sorted(required_volumes)}")
    for role, volume in volumes.items():
        _exact(volume, {"name", "class"}, f"volumes.{role}")
        if volume["class"] not in {"durable", "cache", "transient"}:
            raise RuntimeError(f"volumes.{role}.class is invalid")
        if not isinstance(volume["name"], str) or not volume["name"]:
            raise RuntimeError(f"volumes.{role}.name is required")

    paths = root["paths"]
    required_paths = {"sign_secrets", "sign_evidence"}
    if not isinstance(paths, dict) or not required_paths <= set(paths):
        raise RuntimeError(f"target paths must include {sorted(required_paths)}")
    for role, definition in paths.items():
        _exact(definition, {"path", "class", "required"}, f"paths.{role}")
        if definition["class"] not in {"durable", "cache", "transient"}:
            raise RuntimeError(f"paths.{role}.class is invalid")
        if not isinstance(definition["path"], str) or not definition["path"].startswith("/"):
            raise RuntimeError(f"paths.{role}.path must be absolute")
        if not isinstance(definition["required"], bool):
            raise RuntimeError(f"paths.{role}.required must be boolean")
    sign_root = Path(paths["sign_secrets"]["path"])
    evidence_root = Path(paths["sign_evidence"]["path"])
    if sign_root == evidence_root or sign_root in evidence_root.parents or evidence_root in sign_root.parents:
        raise RuntimeError("Sign secrets and evidence paths must not overlap")

    if not isinstance(root["external_networks"], dict) or not all(
        isinstance(key, str) and isinstance(value, str) and key and value
        for key, value in root["external_networks"].items()
    ):
        raise RuntimeError("external_networks must map roles to names")
    if not isinstance(root["endpoints"], dict) or not all(
        isinstance(key, str) and isinstance(value, str) and value.startswith(("http://", "https://"))
        for key, value in root["endpoints"].items()
    ):
        raise RuntimeError("endpoints must contain HTTP URLs")

    ingress = _exact(
        root["ingress"],
        {"proxy_mode", "list_db", "dbfilter", "websocket"},
        "ingress",
    )
    for field in ("proxy_mode", "list_db", "websocket"):
        if not isinstance(ingress[field], bool):
            raise RuntimeError(f"ingress.{field} must be boolean")
    if not isinstance(ingress["dbfilter"], str) or not ingress["dbfilter"]:
        raise RuntimeError("ingress.dbfilter is required")
    if ingress["proxy_mode"] and not root["endpoints"]["odoo"].startswith("https://"):
        raise RuntimeError("proxy-mode targets require a public HTTPS Odoo endpoint")

    ollama = _exact(
        root["ollama"],
        {"mode", "container", "network", "url", "model", "manifest_sha256", "dimension"},
        "ollama",
    )
    if ollama["mode"] not in {"native", "external"}:
        raise RuntimeError("ollama.mode must be native or external")
    if not SHA256.fullmatch(str(ollama["manifest_sha256"])) or ollama["dimension"] != 1024:
        raise RuntimeError("Ollama identity is invalid")

    backup = _exact(root["backup"], {"durable_repository", "cache_repository"}, "backup")
    for key, value in backup.items():
        if not isinstance(value, str) or not value.startswith(("s3:", "rest:")):
            raise RuntimeError(f"backup.{key} is not a supported Restic repository")
    if backup["durable_repository"] == backup["cache_repository"]:
        raise RuntimeError("durable and cache repositories must differ")
    if not isinstance(root["release_manifest"], str) or not root["release_manifest"].startswith("/"):
        raise RuntimeError("release_manifest must be absolute")

    secrets = _exact(root["secrets"], {"env_file", "allowed_keys"}, "secrets")
    if not isinstance(secrets["env_file"], str) or not secrets["env_file"]:
        raise RuntimeError("secrets.env_file is required")
    allowed = _strings(secrets["allowed_keys"], "secrets.allowed_keys")
    for key in allowed:
        if not SECRET_KEY.fullmatch(key) or is_topology_key(key):
            raise RuntimeError(f"secret allowlist contains a topology key: {key}")
    if not isinstance(root["state_directory"], str) or not root["state_directory"].startswith("/"):
        raise RuntimeError("state_directory must be absolute")
    return Target(path, root)


def load_target(name: str, directory: Path) -> Target:
    path = directory / f"{name}.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"cannot read target definition: {path}") from error
    return validate_target(payload, path)


def is_topology_key(key: str) -> bool:
    return key in RESERVED_SECRET_KEYS or key.endswith(RESERVED_SECRET_SUFFIXES)


def validate_secret_text(text: str, allowed_keys: list[str]) -> list[str]:
    found: list[str] = []
    allowed = set(allowed_keys)
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise RuntimeError(f"secret line {number} is not KEY=VALUE")
        key = line.split("=", 1)[0].strip()
        if not SECRET_KEY.fullmatch(key):
            raise RuntimeError(f"secret line {number} has an invalid key")
        if is_topology_key(key):
            raise RuntimeError(f"secret file contains topology key: {key}")
        if key not in allowed:
            raise RuntimeError(f"secret key is not allowlisted: {key}")
        found.append(key)
    return sorted(found)


def _json_lines(output: str) -> list[dict[str, Any]]:
    values = []
    for line in output.splitlines():
        if line.strip():
            value = json.loads(line)
            values.extend(value if isinstance(value, list) else [value])
    return values


def compose_identity(target: Target, runner: Runner) -> dict[str, Any]:
    anchor = target.value["compose"]["anchor_service"]
    process = runner.run(
        [
            "docker",
            "ps",
            "-a",
            "--filter",
            f"label=com.docker.compose.project={target.project}",
            "--filter",
            f"label=com.docker.compose.service={anchor}",
            "--format",
            "{{.ID}}",
        ],
    )
    identifiers = [line for line in process.stdout.splitlines() if line]
    if len(identifiers) != 1:
        raise RuntimeError(
            f"expected one {target.project}/{anchor} container, found {len(identifiers)}",
        )
    inspect = runner.run(
        ["docker", "inspect", identifiers[0], "--format", "{{json .Config.Labels}}"],
    )
    labels = json.loads(inspect.stdout)
    if labels.get("com.docker.compose.project") != target.project:
        raise RuntimeError("anchor container belongs to another Compose project")
    files = [item for item in labels.get("com.docker.compose.project.config_files", "").split(",") if item]
    directory = labels.get("com.docker.compose.project.working_dir", "")
    env_file = labels.get("com.docker.compose.project.environment_file", "")
    env_files = [item for item in env_file.split(",") if item]
    if (
        not files
        or not directory.startswith("/")
        or not env_files
        or any(not item.startswith("/") for item in env_files)
    ):
        raise RuntimeError("anchor container has incomplete Compose provenance")
    return {
        "container_id": identifiers[0],
        "project": target.project,
        "working_directory": directory,
        "compose_files": files,
        "environment_file": env_file,
        "profiles": target.value["compose"]["profiles"],
    }


def compose_command(identity: dict[str, Any], arguments: list[str]) -> list[str]:
    command = [
        "docker",
        "compose",
        "--project-name",
        identity["project"],
        "--project-directory",
        identity["working_directory"],
    ]
    for path in identity["environment_file"].split(","):
        command.extend(("--env-file", path))
    for path in identity["compose_files"]:
        command.extend(("--file", path))
    for profile in identity.get("profiles", []):
        command.extend(("--profile", profile))
    return [*command, *arguments]


def read_active_state(target: Target, runner: Runner) -> dict[str, Any] | None:
    path = f"{target.value['state_directory']}/active.json"
    process = runner.run(["cat", path], check=False)
    if process.returncode:
        return None
    try:
        state = json.loads(process.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"active generation state is invalid: {path}") from error
    expected = {
        "schema",
        "target",
        "generation",
        "volumes",
        "network",
        "snapshot",
        "release_manifest",
        "previous",
    }
    if not isinstance(state, dict) or set(state) != expected:
        raise RuntimeError("active generation state fields differ")
    if state["schema"] != "usl-active-generation/v1" or state["target"] != target.name:
        raise RuntimeError("active generation state belongs to another target")
    if not TARGET_NAME.fullmatch(str(state["generation"])):
        raise RuntimeError("active generation name is invalid")
    if set(state["volumes"]) != set(target.value["volumes"]):
        raise RuntimeError("active generation volume perimeter differs")
    if not isinstance(state["network"], str) or not state["network"]:
        raise RuntimeError("active generation network is invalid")
    if not SHA256.fullmatch(str(state["snapshot"])):
        raise RuntimeError("active generation snapshot is invalid")
    if not isinstance(state["release_manifest"], str) or not state["release_manifest"].startswith(
        target.value["state_directory"] + "/generations/"
    ):
        raise RuntimeError("active generation release manifest is invalid")
    return state


def effective_volumes(target: Target, runner: Runner) -> tuple[dict[str, dict[str, str]], str | None]:
    state = read_active_state(target, runner)
    if state is None:
        return target.value["volumes"], None
    volumes = {
        role: {"name": name, "class": target.value["volumes"][role]["class"]}
        for role, name in state["volumes"].items()
        if isinstance(name, str) and name
    }
    if set(volumes) != set(target.value["volumes"]):
        raise RuntimeError("active generation contains an invalid volume name")
    return volumes, state["generation"]


def inspect_runtime(target: Target, runner: Runner) -> dict[str, Any]:
    identity = compose_identity(target, runner)
    process = runner.run(compose_command(identity, ["ps", "--all", "--format", "json"]))
    containers = _json_lines(process.stdout)
    foreign = [
        item.get("Name")
        for item in containers
        if item.get("Project") not in {None, "", target.project}
    ]
    if foreign:
        raise RuntimeError(f"Compose returned foreign containers: {foreign}")
    active_state = read_active_state(target, runner)
    definitions, generation = effective_volumes(target, runner)
    volumes = {}
    for role, definition in definitions.items():
        inspected = runner.run(
            ["docker", "volume", "inspect", definition["name"], "--format", "{{json .Labels}}"],
        )
        labels = json.loads(inspected.stdout)
        legacy_owner = labels.get("com.docker.compose.project") == target.project
        generation_owner = (
            labels.get("com.unstaticlabs.runtime.project") == target.project
            and labels.get("com.unstaticlabs.runtime.target") == target.name
            and labels.get("com.unstaticlabs.runtime.generation") == generation
            and labels.get("com.unstaticlabs.runtime.role") == role
        )
        if not legacy_owner and not generation_owner:
            raise RuntimeError(f"volume {definition['name']} is not owned by {target.project}")
        volumes[role] = {"name": definition["name"], "class": definition["class"]}
    return {
        "schema": "usl-runtime-status/v1",
        "target": target.name,
        "environment": target.value["environment"],
        "protected": target.protected,
        "compose": identity,
        "containers": containers,
        "volumes": volumes,
        "generation": generation or "adopted",
        "active_state": active_state,
        "paths": target.value["paths"],
    }
