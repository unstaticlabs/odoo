"""Validated runtime identity and exact Docker ownership for migration/manage."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import re
import secrets as secret_generator
import shutil
import stat
import subprocess
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

from migration.digests import tree_digest


SCHEMA = "usl-migration-runtime-v1"
MODEL = "usl-bge-m3:documents-20260824-rc1"
MODEL_MANIFEST_SHA256 = "7907646426070047a77226ac3e684fbbe8410524f7b4a74d02837e43f2146bab"
SAFE_ID = re.compile(r"[a-z0-9][a-z0-9-]{2,63}\Z")
SAFE_PROJECT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{2,127}\Z")
SAFE_DATABASE = re.compile(r"[A-Za-z0-9_]{3,63}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")

SECRET_KEYS = frozenset(
    {
        "ODOO_ADMIN_PASSWORD",
        "ODOO_DB_PASSWORD",
        "ODOO_MCP_BETTER_AUTH_SECRET",
        "ODOO_MCP_CREDENTIAL_ENCRYPTION_KEY",
        "ODOO_SMTP_PASSWORD",
        "PAPERLESS_DB_PASSWORD",
        "PAPERLESS_SECRET_KEY",
        "POCKET_ID_CLIENT_SECRET",
        "POCKET_ID_ENCRYPTION_KEY",
        "POCKET_ID_PAPERLESS_CLIENT_SECRET",
        "POCKET_ID_SIGN_CLIENT_SECRET",
        "POCKET_ID_STATIC_API_KEY",
        "POSTGRES_PASSWORD",
        "USL_POCKET_ID_BREAK_GLASS_PASSWORD",
        # Stable identity values belong with the private identity material.
        "POCKET_ID_PROSPER_EMAIL",
        "POCKET_ID_PROSPER_ODOO_EMAIL",
        "POCKET_ID_PROSPER_ID",
        "POCKET_ID_ROGER_ID",
        "POCKET_ID_VALENTIN_ID",
    }
)
IDENTITY_DEFAULTS = {
    "PAPERLESS_ACCOUNT_DEFAULT_HTTP_PROTOCOL": "http",
    "POCKET_ID_CLIENT_ID": "usl-odoo-preproduction",
    "POCKET_ID_GROUP_NAME": "odoo-preproduction",
    "POCKET_ID_PAPERLESS_CLIENT_ID": "usl-paperless-preproduction",
    "POCKET_ID_SIGN_CLIENT_ID": "usl-sign-authorization",
    "POCKET_ID_SIGN_GROUP_NAME": "usl-signers",
}
SCOPE_KEYS = frozenset(
    {
        "COMPOSE_PROJECT_NAME",
        "ODOO_DB_NAME",
        "ODOO_DB_FILTER",
        "ODOO_DEV_DB",
        "ODOO_GEVENT_PORT",
        "ODOO_HTTP_PORT",
        "ODOO_IMAGE",
        "ODOO_MCP_ALLOWED_HOSTS",
        "ODOO_MCP_ALLOWED_ORIGINS",
        "ODOO_MCP_ALLOW_LOCAL_HTTP_ODOO",
        "ODOO_MCP_BETTER_AUTH_SECRET_FILE",
        "ODOO_MCP_CREDENTIAL_ENCRYPTION_KEY_FILE",
        "ODOO_MCP_HTTP_PORT",
        "ODOO_MCP_IMAGE",
        "ODOO_MCP_OAUTH_TRUSTED_ORIGINS",
        "ODOO_MCP_PUBLIC_ORIGIN",
        "ODOO_MCP_RELEASE_COMMIT",
        "ODOO_INIT_DB",
        "ODOO_PUBLIC_BASE_URL",
        "OLLAMA_IMAGE",
        "PAPERLESS_HTTP_PORT",
        "PAPERLESS_IMAGE",
        "PAPERLESS_PUBLIC_BASE_URL",
        "PAPERLESS_PUBLIC_URL",
        "POCKET_ID_APP_URL",
        "POCKET_ID_HTTP_PORT",
        "POCKET_ID_IMAGE",
        "USL_NATIVE_OLLAMA_CONTAINER_URL",
        "USL_NATIVE_OLLAMA_HOST_URL",
        "USL_DOCUMENTS_MCP_REPOSITORY",
        "USL_ODOO_MCP_OAUTH_VOLUME",
        "USL_OLLAMA_RUNTIME",
        "USL_ONLINE_DUMP_DIR",
    }
)


class RuntimeError(ValueError):
    """Raised before an ambiguous or unsafe runtime operation."""


@dataclass(frozen=True)
class Completed:
    returncode: int
    stdout: str = ""
    stderr: str = ""


class CommandRunner:
    """Small injectable command boundary used by behavior tests."""

    def run(
        self,
        arguments: Iterable[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        check: bool = True,
    ) -> Completed:
        process = subprocess.run(
            tuple(arguments),
            cwd=cwd,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        result = Completed(process.returncode, process.stdout, process.stderr)
        if check and result.returncode:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip())
        return result


def now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_identity(source: Path, expected: str | None = None) -> dict[str, Any]:
    source = source.expanduser().resolve()
    dump = source / "dump.sql"
    filestore = source / "filestore"
    if not dump.is_file() or not filestore.is_dir():
        raise RuntimeError(f"frozen source package is incomplete: {source}")
    actual = sha256_file(dump)
    if expected and (not SHA256.fullmatch(expected) or expected != actual):
        raise RuntimeError("frozen source dump checksum does not match the confirmed identity")
    filestore_sha256, file_count, filestore_size = tree_digest(filestore)
    return {
        "path": str(source),
        "dump_sha256": actual,
        "dump_size": dump.stat().st_size,
        "filestore_file_count": file_count,
        "filestore_size": filestore_size,
        "filestore_sha256": filestore_sha256,
    }


def private_file(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"private input must be a regular file: {path}")
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise RuntimeError(f"private input must be mode 0600: {path}")


def parse_assignments(path: Path) -> dict[str, str]:
    private_file(path)
    values: dict[str, str] = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if (
            not separator
            or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key)
            or key in values
            or any(character in value for character in ("\x00", "\r", "\n"))
        ):
            raise RuntimeError(f"invalid or duplicate private assignment at line {number}")
        values[key] = value
    return values


def read_secrets(path: Path) -> dict[str, str]:
    values = parse_assignments(path)
    reserved = sorted(set(values) & SCOPE_KEYS)
    if reserved:
        raise RuntimeError("secret file contains runtime scope fields: " + ", ".join(reserved))
    unsupported = sorted(set(values) - SECRET_KEYS)
    if unsupported:
        raise RuntimeError("secret file contains unsupported keys: " + ", ".join(unsupported))
    return values


def ensure_mcp_secrets(values: dict[str, str]) -> dict[str, str]:
    """Resolve independent OAuth secrets once for a runtime."""
    result = dict(values)
    result.setdefault(
        "ODOO_MCP_BETTER_AUTH_SECRET",
        secret_generator.token_urlsafe(48),
    )
    result.setdefault(
        "ODOO_MCP_CREDENTIAL_ENCRYPTION_KEY",
        base64.b64encode(secret_generator.token_bytes(32)).decode("ascii"),
    )
    if len(result["ODOO_MCP_BETTER_AUTH_SECRET"]) < 32:
        raise RuntimeError("Odoo MCP authentication secret must contain at least 32 characters")
    try:
        encryption_key = base64.b64decode(
            result["ODOO_MCP_CREDENTIAL_ENCRYPTION_KEY"], validate=True
        )
    except ValueError as error:
        raise RuntimeError("Odoo MCP credential key must be valid base64") from error
    if len(encryption_key) != 32:
        raise RuntimeError("Odoo MCP credential key must encode exactly 32 bytes")
    return result


def write_private(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        path.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)


def write_json(path: Path, value: dict[str, Any]) -> None:
    write_private(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


class RuntimeStore:
    def __init__(self, repository: Path):
        self.repository = repository.resolve()
        self.root = self.repository / "private/migration/runtimes"

    def directory(self, runtime_id: str) -> Path:
        if not SAFE_ID.fullmatch(runtime_id):
            raise RuntimeError("runtime ID must use 3-64 lowercase letters, digits, or hyphens")
        return self.root / runtime_id

    def create(self, value: dict[str, Any], secrets: dict[str, str]) -> dict[str, Any]:
        secrets = ensure_mcp_secrets(secrets)
        directory = self.directory(value["id"])
        if directory.exists():
            raise RuntimeError(f"runtime already exists: {value['id']}")
        directory.mkdir(parents=True, mode=0o700)
        (self.repository / "private").chmod(0o700)
        (self.repository / "private/migration").chmod(0o700)
        directory.chmod(0o700)
        self.root.chmod(0o700)
        write_json(directory / "runtime.json", value)
        assignments = "".join(f"{key}={secrets[key]}\n" for key in sorted(secrets))
        write_private(directory / "secrets.env", assignments)
        write_private(
            directory / "odoo-mcp-better-auth.secret",
            secrets["ODOO_MCP_BETTER_AUTH_SECRET"] + "\n",
        )
        write_private(
            directory / "odoo-mcp-credential-encryption-key.secret",
            secrets["ODOO_MCP_CREDENTIAL_ENCRYPTION_KEY"] + "\n",
        )
        return value

    def load(self, runtime_id: str) -> dict[str, Any]:
        directory = self.directory(runtime_id)
        path = directory / "runtime.json"
        private_file(path)
        if stat.S_IMODE(directory.stat().st_mode) != 0o700:
            raise RuntimeError(f"runtime directory must be mode 0700: {directory}")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise RuntimeError(f"runtime state is invalid JSON: {path}") from error
        if value.get("schema") != SCHEMA or value.get("id") != runtime_id:
            raise RuntimeError(f"runtime identity is invalid: {runtime_id}")
        return value

    def save(self, value: dict[str, Any]) -> None:
        self.load(value["id"])
        value["updated_at"] = now()
        write_json(self.directory(value["id"]) / "runtime.json", value)

    def secrets(self, runtime_id: str) -> dict[str, str]:
        return read_secrets(self.directory(runtime_id) / "secrets.env")

    def write_secrets(self, runtime_id: str, name: str, secrets: dict[str, str]) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9-]{1,31}", name):
            raise RuntimeError("unsafe private secret-set name")
        self.load(runtime_id)
        assignments = "".join(f"{key}={secrets[key]}\n" for key in sorted(secrets))
        write_private(self.directory(runtime_id) / f"{name}.env", assignments)

    def named_secrets(self, runtime_id: str, name: str) -> dict[str, str]:
        return read_secrets(self.directory(runtime_id) / f"{name}.env")


def _json_output(runner: CommandRunner, arguments: list[str]) -> list[dict[str, Any]]:
    output = runner.run(arguments).stdout.strip()
    if not output:
        return []
    try:
        value = json.loads(output)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"invalid Docker inspection output for {' '.join(arguments[:3])}") from error
    if not isinstance(value, list):
        raise RuntimeError("Docker inspection must return a JSON list")
    return value


def inspect_project(runner: CommandRunner, project: str, workdir: Path) -> dict[str, Any]:
    if not SAFE_PROJECT.fullmatch(project):
        raise RuntimeError("unsafe Compose project name")
    container_ids = runner.run(
        ["docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={project}"]
    ).stdout.split()
    containers = _json_output(runner, ["docker", "inspect", *container_ids]) if container_ids else []
    normalized_containers = []
    expected_workdir = str(workdir.resolve())
    for item in containers:
        labels = (item.get("Config") or {}).get("Labels") or {}
        if labels.get("com.docker.compose.project") != project:
            raise RuntimeError("Docker returned a foreign container for the runtime")
        owner = labels.get("com.docker.compose.project.working_dir")
        if not owner or str(Path(owner).resolve()) != expected_workdir:
            raise RuntimeError(f"Compose project contains a foreign working-directory owner: {owner}")
        normalized_containers.append(
            {
                "id": item.get("Id"),
                "name": str(item.get("Name", "")).lstrip("/"),
                "service": labels.get("com.docker.compose.service"),
                "state": ((item.get("State") or {}).get("Status")),
                "health": (((item.get("State") or {}).get("Health") or {}).get("Status")),
                "exit_code": ((item.get("State") or {}).get("ExitCode")),
                "image": (item.get("Image") or (item.get("Config") or {}).get("Image")),
                "configured_image": (item.get("Config") or {}).get("Image"),
                "release_commit": labels.get("org.opencontainers.image.revision"),
                "working_dir": owner,
                "mounts": sorted(
                    (
                        {
                            "type": mount.get("Type"),
                            "source": mount.get("Name") or mount.get("Source"),
                            "destination": mount.get("Destination"),
                        }
                        for mount in item.get("Mounts") or []
                    ),
                    key=lambda mount: (
                        str(mount["destination"]),
                        str(mount["source"]),
                    ),
                ),
            }
        )
    volume_names = runner.run(
        ["docker", "volume", "ls", "-q", "--filter", f"label=com.docker.compose.project={project}"]
    ).stdout.split()
    volumes = _json_output(runner, ["docker", "volume", "inspect", *volume_names]) if volume_names else []
    normalized_volumes = []
    for item in volumes:
        labels = item.get("Labels") or {}
        if labels.get("com.docker.compose.project") != project:
            raise RuntimeError("Docker returned a foreign volume for the runtime")
        normalized_volumes.append({"name": item.get("Name"), "mountpoint": item.get("Mountpoint")})
    network_ids = runner.run(
        ["docker", "network", "ls", "-q", "--filter", f"label=com.docker.compose.project={project}"]
    ).stdout.split()
    networks = _json_output(runner, ["docker", "network", "inspect", *network_ids]) if network_ids else []
    normalized_networks = []
    for item in networks:
        labels = item.get("Labels") or {}
        if labels.get("com.docker.compose.project") != project:
            raise RuntimeError("Docker returned a foreign network for the runtime")
        normalized_networks.append({"id": item.get("Id"), "name": item.get("Name")})
    return {
        "containers": sorted(normalized_containers, key=lambda item: str(item["id"])),
        "volumes": sorted(normalized_volumes, key=lambda item: str(item["name"])),
        "networks": sorted(normalized_networks, key=lambda item: str(item["id"])),
    }


def verify_recorded_resources(recorded: dict[str, Any], current: dict[str, Any]) -> None:
    for kind, key in (("containers", "id"), ("volumes", "name"), ("networks", "id")):
        expected = {item[key] for item in recorded.get(kind, [])}
        actual = {item[key] for item in current.get(kind, [])}
        if expected != actual:
            raise RuntimeError(f"runtime {kind} differ from the recorded exact resource set")


def _native_manifest(models: Path) -> Path:
    return models / "manifests/registry.ollama.ai/library/usl-bge-m3/documents-20260824-rc1"


def resolve_ollama(
    requested: str,
    *,
    system: str | None = None,
    executable: str | None = None,
    models: Path | None = None,
    host_url: str = "http://127.0.0.1:11434",
    reachable: bool | None = None,
) -> dict[str, str]:
    if requested not in {"auto", "native", "container"}:
        raise RuntimeError("Ollama mode must be auto, native, or container")
    system = system or platform.system()
    executable = executable if executable is not None else shutil.which("ollama")
    native_available = system == "Darwin" and bool(executable)
    if requested == "container":
        return {"mode": "container", "model": MODEL, "manifest_sha256": MODEL_MANIFEST_SHA256}
    if native_available:
        if reachable is None:
            try:
                with urllib.request.urlopen(f"{host_url}/api/version", timeout=5) as response:
                    reachable = response.status == 200
            except (OSError, urllib.error.URLError):
                reachable = False
        if not reachable:
            raise RuntimeError("native macOS Ollama is installed but unreachable; container fallback is forbidden")
        models = models or Path.home() / ".ollama/models"
        manifest = _native_manifest(models)
        if not manifest.is_file() or sha256_file(manifest) != MODEL_MANIFEST_SHA256:
            raise RuntimeError(f"native Ollama does not contain the qualified {MODEL} model identity")
        return {
            "mode": "native",
            "model": MODEL,
            "manifest_sha256": MODEL_MANIFEST_SHA256,
            "host_url": host_url,
            "container_url": "http://host.docker.internal:11434",
            "models_path": str(models.resolve()),
        }
    if requested == "native":
        raise RuntimeError("native Ollama was requested but is unavailable")
    return {"mode": "container", "model": MODEL, "manifest_sha256": MODEL_MANIFEST_SHA256}


def compose_files(repository: Path, kind: str, ollama_mode: str) -> list[str]:
    if kind in {"qa", "transition"}:
        result = ["compose.yaml"]
        if kind == "transition":
            result.append("compose.production.yaml")
        result.append("compose.pocket-id.yaml")
        if ollama_mode == "native":
            result.append("compose.ollama-native.yaml")
        return [str((repository / item).resolve()) for item in result]
    result = ["compose.yaml", "compose.production.yaml", "compose.external-pocket-id.yaml"]
    return [str((repository / item).resolve()) for item in result]


def runtime_environment(runtime: dict[str, Any], secrets: dict[str, str]) -> dict[str, str]:
    keep = {"PATH", "HOME", "USER", "TMPDIR", "LANG", "SHELL", "TERM", "DOCKER_HOST", "DOCKER_CONTEXT"}
    environment = {key: value for key, value in os.environ.items() if key in keep or key.startswith("LC_")}
    ports = runtime["ports"]
    urls = runtime["urls"]
    odoo_url = urlparse(urls["odoo"])
    odoo_host = odoo_url.hostname or ""
    allow_local_http_odoo = odoo_url.scheme == "http" and (
        odoo_host in {"localhost", "127.0.0.1", "::1"}
        or odoo_host.endswith(".localhost")
    )
    mcp_secret_directory = Path(runtime["private_directory"])
    environment.update(
        {
            "COMPOSE_PROJECT_NAME": runtime["compose"]["project"],
            "COMPOSE_FILE": os.pathsep.join(runtime["compose"]["files"]),
            "ODOO_DEV_DB": runtime["database"],
            "ODOO_DB_NAME": runtime["database"],
            "ODOO_DB_FILTER": f"^{re.escape(runtime['database'])}$",
            "ODOO_INIT_DB": runtime["database"],
            "ODOO_HTTP_PORT": str(ports["odoo"]),
            "ODOO_GEVENT_PORT": str(ports["gevent"]),
            "PAPERLESS_HTTP_PORT": str(ports["paperless"]),
            "POCKET_ID_HTTP_PORT": str(ports["pocket_id"]),
            "ODOO_PUBLIC_BASE_URL": urls["odoo"],
            "PAPERLESS_PUBLIC_URL": urls["paperless"],
            "PAPERLESS_PUBLIC_BASE_URL": urls["paperless"],
            "POCKET_ID_APP_URL": urls["pocket_id"],
            "ODOO_MCP_ALLOW_LOCAL_HTTP_ODOO": (
                "true" if allow_local_http_odoo else "false"
            ),
            "COMPOSE_PROFILES": ",".join(runtime["compose"].get("profiles", [])),
            "USL_ONLINE_DUMP_DIR": runtime["source"]["path"],
            "USL_MIGRATION_SOURCE_SHA256": runtime["source"]["dump_sha256"],
            "USL_EINVOICE_LIVE_ENABLED": "0",
            "USL_EREPORTING_LIVE_ENABLED": "0",
            "USL_OLLAMA_RUNTIME": runtime["ollama"]["mode"],
            "USL_OLLAMA_RUNTIME_SELECTED": runtime["ollama"]["mode"],
            "USL_OLLAMA_COMPOSE_OVERRIDE": (
                str(Path(runtime["compose"]["working_directory"]) / "compose.ollama-native.yaml")
                if runtime["ollama"]["mode"] == "native"
                else ""
            ),
            "USL_MIGRATION_RUNTIME_ID": runtime["id"],
            "USL_MIGRATION_RUNTIME_STATE": str(
                Path(runtime["private_directory"]) / "runtime.json"
            ),
            "USL_DOCUMENTS_RELEASE_SOURCE_PROJECT": runtime["compose"]["project"],
            "USL_DOCUMENTS_RELEASE_SOURCE_DATABASE": runtime["database"],
            "USL_DOCUMENTS_RESTORE_DATABASE": runtime["database"],
            "USL_PERSONAL_AI_MASTER_KEYS_HOST_PATH": runtime["personal_ai_key_file"],
            "DOCUMENTS_PAPERLESS_TASK_WORKERS": str(
                runtime.get("documents", {}).get("paperless_task_workers", 3)
            ),
            "PAPERLESS_TASK_WORKERS": str(
                runtime.get("documents", {}).get("paperless_task_workers", 3)
            ),
            "PAPERLESS_AI_LLM_EMBEDDING_BATCH_SIZE": str(
                runtime.get("documents", {}).get("embedding_batch_size", 32)
            ),
            "PAPERLESS_AI_LLM_EMBEDDING_CHUNK_SIZE": str(
                runtime.get("documents", {}).get("embedding_chunk_size", 512)
            ),
            **IDENTITY_DEFAULTS,
        }
    )
    mcp = runtime.get("mcp")
    mcp_url = urls.get("mcp")
    mcp_port = ports.get("mcp")
    if any(value is not None for value in (mcp, mcp_url, mcp_port)):
        if not mcp or not mcp_url or mcp_port is None:
            raise RuntimeError("runtime has an incomplete Odoo MCP identity")
        mcp_host = urlparse(mcp_url).hostname or ""
        environment.update(
            {
                "ODOO_MCP_HTTP_PORT": str(mcp_port),
                "ODOO_MCP_PUBLIC_ORIGIN": mcp_url,
                "ODOO_MCP_ALLOWED_HOSTS": ",".join(
                    dict.fromkeys((mcp_host, "localhost", "127.0.0.1"))
                ),
                "ODOO_MCP_ALLOWED_ORIGINS": ",".join(
                    dict.fromkeys(
                        (
                            "chatgpt.com",
                            "claude.ai",
                            mcp_host,
                            "localhost",
                            "127.0.0.1",
                        )
                    )
                ),
                "ODOO_MCP_OAUTH_TRUSTED_ORIGINS": ",".join(
                    dict.fromkeys(("https://chatgpt.com", "https://claude.ai", mcp_url))
                ),
                "ODOO_MCP_BETTER_AUTH_SECRET_FILE": str(
                    mcp_secret_directory / "odoo-mcp-better-auth.secret"
                ),
                "ODOO_MCP_CREDENTIAL_ENCRYPTION_KEY_FILE": str(
                    mcp_secret_directory / "odoo-mcp-credential-encryption-key.secret"
                ),
                "ODOO_MCP_IMAGE": mcp["image"],
                "ODOO_MCP_RELEASE_COMMIT": mcp["commit"],
                "USL_DOCUMENTS_MCP_REPOSITORY": mcp["checkout"],
            }
        )
    if runtime["ollama"]["mode"] == "native":
        environment.update(
            {
                "USL_NATIVE_OLLAMA_HOST_URL": runtime["ollama"]["host_url"],
                "USL_NATIVE_OLLAMA_CONTAINER_URL": runtime["ollama"]["container_url"],
            }
        )
    local_production_overrides = [
        path
        for path in runtime["compose"]["files"]
        if Path(path).name == "compose.production.yaml"
    ]
    if len(local_production_overrides) > 1:
        raise RuntimeError("runtime has multiple local production Compose overrides")
    if local_production_overrides:
        environment["USL_POCKET_ID_COMPOSE_EXTRA_FILE"] = local_production_overrides[0]
    images = runtime.get("images") or {}
    # Docker inspection reports local content IDs (``sha256:...``) for
    # adopted containers. They are useful evidence, but not stable Compose
    # image references: a rebuild can replace the untagged image before the
    # new container is created. Only explicit named references may override
    # the checkout's normal build tags.
    if images.get("odoo") and not images["odoo"].startswith("sha256:"):
        environment["ODOO_IMAGE"] = images["odoo"]
        environment["USL_CANDIDATE_IMAGE"] = images["odoo"]
    if images.get("paperless-webserver") and not images[
        "paperless-webserver"
    ].startswith("sha256:"):
        environment["PAPERLESS_IMAGE"] = images["paperless-webserver"]
    if images.get("paperless-ollama") and not images["paperless-ollama"].startswith(
        "sha256:"
    ):
        environment["OLLAMA_IMAGE"] = images["paperless-ollama"]
    environment.update(secrets)
    return environment


def identity_values(runtime: dict[str, Any], secrets: dict[str, str]) -> dict[str, str]:
    values = runtime_environment(runtime, secrets)
    values.update(
        {
            "COMPOSE_PROJECT_NAME": runtime["compose"]["project"],
            "ODOO_INIT_DB": runtime["database"],
        }
    )
    return values


def sanitize_adopted_identity(path: Path, expected: dict[str, str]) -> dict[str, str]:
    """Import only private identity material from a legacy runtime environment."""
    values = parse_assignments(path)
    for key, expected_value in expected.items():
        if key in values and values[key] != expected_value:
            raise RuntimeError(f"adopted identity scope conflicts with {key}")
    secrets = {key: value for key, value in values.items() if key in SECRET_KEYS}
    missing = sorted(
        {
            "POCKET_ID_CLIENT_SECRET",
            "POCKET_ID_ENCRYPTION_KEY",
            "POCKET_ID_STATIC_API_KEY",
            "POCKET_ID_VALENTIN_ID",
        }
        - set(secrets)
    )
    if missing:
        raise RuntimeError("adopted runtime identity is missing: " + ", ".join(missing))
    return secrets
