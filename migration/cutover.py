#!/usr/bin/env python3
"""Fail-closed validation and state transitions for production cut-over."""

# ruff: noqa: EM101, T201, TRY301

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import stat
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from migration.mcp_release import load_release
from scripts.distribution_release import (
    ReleaseArtifactError,
    validate as validate_distribution_release,
)

STATE_SCHEMA = "usl-production-cutover-state-v1"
POLICY_SCHEMA = "usl-external-identity-policy-v1"
JOURNEY_SCHEMA = "usl-production-browser-journeys-v1"
FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")
IMAGE = re.compile(r"^.+@sha256:[0-9a-f]{64}$")
SAFE_DATABASE = re.compile(r"^[a-z][a-z0-9_]{2,62}$")
SAFE_PROJECT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,127}$")
VOLUME_KEYS = (
    "USL_ODOO_POSTGRES_VOLUME",
    "USL_ODOO_DATA_VOLUME",
    "USL_PAPERLESS_POSTGRES_VOLUME",
    "USL_PAPERLESS_BROKER_VOLUME",
    "USL_PAPERLESS_DATA_VOLUME",
    "USL_PAPERLESS_MEDIA_VOLUME",
    "USL_PAPERLESS_EXPORT_VOLUME",
    "USL_PAPERLESS_CONSUME_VOLUME",
    "USL_PAPERLESS_TRASH_VOLUME",
    "USL_ODOO_MCP_OAUTH_VOLUME",
)
SIGN_SECRET_DIRECTORIES = {
    "USL_DOCUMENT_RENDERER_CERT_DIR": (
        "ca.crt",
        "odoo.crt",
        "odoo.key",
        "renderer.crt",
        "renderer.key",
    ),
    "USL_SIGN_STEP_CA_DIR": (
        "certs/root_ca.crt",
        "config/ca.json",
        "password",
        "secrets/intermediate_ca_key",
    ),
    "USL_SIGN_DSS_SECRET_DIR": (
        "client-trust.p12",
        "local-trust.p12",
        "manifest.p12",
        "platform.p12",
        "server.p12",
    ),
    "USL_SIGN_ODOO_SECRET_DIR": (
        "client-chain.crt",
        "client.key",
        "provisioner.jwk",
        "root_ca.crt",
    ),
}
REQUIRED = (
    "COMPOSE_PROJECT_NAME",
    "ODOO_ADMIN_PASSWORD",
    "ODOO_DB_MAXCONN",
    "ODOO_DB_FILTER",
    "ODOO_INIT_DB",
    "ODOO_LIST_DB",
    "ODOO_LIMIT_MEMORY_HARD",
    "ODOO_LIMIT_MEMORY_SOFT",
    "ODOO_LIMIT_REQUEST",
    "ODOO_MAX_CRON_THREADS",
    "ODOO_DB_NAME",
    "ODOO_DB_PASSWORD",
    "ODOO_GEVENT_PORT",
    "ODOO_HTTP_PORT",
    "ODOO_IMAGE",
    "ODOO_MCP_BETTER_AUTH_SECRET_FILE",
    "ODOO_MCP_CREDENTIAL_ENCRYPTION_KEY_FILE",
    "ODOO_MCP_ALLOWED_HOSTS",
    "ODOO_MCP_ALLOWED_ORIGINS",
    "ODOO_MCP_ALLOW_LOCAL_HTTP_ODOO",
    "ODOO_MCP_HTTP_PORT",
    "ODOO_MCP_HEALTHCHECK_HOST",
    "ODOO_MCP_IMAGE",
    "ODOO_MCP_OAUTH_TRUSTED_ORIGINS",
    "ODOO_MCP_PUBLIC_ORIGIN",
    "ODOO_MCP_RELEASE_COMMIT",
    "ODOO_PUBLIC_BASE_URL",
    "ODOO_SMTP_SERVER",
    "ODOO_SMTP_PORT",
    "ODOO_SMTP_SSL",
    "ODOO_SMTP_USER",
    "ODOO_SMTP_PASSWORD",
    "ODOO_EMAIL_FROM",
    "ODOO_FROM_FILTER",
    "ODOO_WORKERS",
    "PAPERLESS_ALLOWED_HOSTS",
    "PAPERLESS_DB_NAME",
    "PAPERLESS_DB_PASSWORD",
    "PAPERLESS_DB_USER",
    "PAPERLESS_HTTP_PORT",
    "PAPERLESS_IMAGE",
    "PAPERLESS_PUBLIC_BASE_URL",
    "PAPERLESS_PUBLIC_URL",
    "PAPERLESS_SECRET_KEY",
    "PAPERLESS_SSO_BASE_GROUP",
    "OLLAMA_IMAGE",
    "POCKET_ID_APP_URL",
    "POCKET_ID_CLIENT_ID",
    "POCKET_ID_CLIENT_SECRET",
    "POCKET_ID_GROUP_NAME",
    "POCKET_ID_PAPERLESS_CLIENT_ID",
    "POCKET_ID_PAPERLESS_CLIENT_SECRET",
    "USL_POCKET_ID_BREAK_GLASS_PASSWORD",
    "USL_DEPLOYMENT_ENV",
    "USL_EXTERNAL_IDENTITY_NETWORK",
    "USL_EXTERNAL_INGRESS_NETWORK",
    "USL_EXTERNAL_OLLAMA_NETWORK",
    "USL_EXTERNAL_OLLAMA_CONTAINER",
    "USL_EINVOICE_LIVE_ENABLED",
    "USL_EREPORTING_LIVE_ENABLED",
    "USL_PRODUCTION_CRON_THREADS",
    "USL_PRODUCTION_CRON_GATES_JSON",
    "USL_PERSONAL_AI_MASTER_KEYS_HOST_PATH",
    "USL_OLLAMA_RUNTIME",
    "USL_OLLAMA_MANIFEST_SHA256",
    "USL_OLLAMA_EMBEDDING_DIMENSION",
    "PAPERLESS_AI_LLM_EMBEDDING_ENDPOINT",
    "PAPERLESS_AI_LLM_EMBEDDING_MODEL",
    "PAPERLESS_AI_LLM_EMBEDDING_BATCH_SIZE",
    "USL_DOCUMENT_RENDERER_CERT_DIR",
    "USL_DOCUMENT_RENDERER_IMAGE",
    "USL_SIGN_DSS_IMAGE",
    "USL_SIGN_DSS_SECRET_DIR",
    "USL_SIGN_EVIDENCE_DIR",
    "USL_SIGN_ODOO_SECRET_DIR",
    "USL_SIGN_STEP_CA_DIR",
    "USL_SIGN_STEP_CA_IMAGE",
    "POSTGRES_PASSWORD",
    *VOLUME_KEYS,
)


class CutoverError(ValueError):
    """Raised before an unsafe cut-over operation can run."""


def private_file(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise CutoverError(f"private input must be a regular file: {path}")
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode != 0o600:
        raise CutoverError(f"private input mode must be 0600: {path} ({mode:o})")


def private_directory(path: Path, required_files: tuple[str, ...]) -> None:
    if path.is_symlink() or not path.is_absolute() or not path.is_dir():
        raise CutoverError(f"private secret directory must be absolute: {path}")
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise CutoverError(f"private secret directory must not be group/world accessible: {path}")
    for relative in required_files:
        required = path / relative
        if required.is_symlink() or not required.is_file() or required.stat().st_size == 0:
            raise CutoverError(f"private secret directory is incomplete: {path}")


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CutoverError(f"cannot read JSON: {path}") from error
    if not isinstance(value, dict):
        raise CutoverError(f"JSON object required: {path}")
    return value


def parse_env(path: Path) -> dict[str, str]:
    private_file(path)
    result = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if (
            not separator
            or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key)
            or key in result
        ):
            raise CutoverError(f"invalid/duplicate environment entry at line {number}")
        if any(character in value for character in ("\x00", "\r", "\n")):
            raise CutoverError(f"unsafe environment value at line {number}")
        result[key] = value
    missing = [key for key in REQUIRED if not result.get(key)]
    if missing:
        raise CutoverError("production environment is missing: " + ", ".join(missing))
    return result


def _production_url(name: str, value: str) -> None:
    parsed = urlsplit(value)
    hostname = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https"
        or not hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or hostname in {"localhost", "127.0.0.1", "::1"}
        or hostname.endswith((".localhost", ".invalid"))
    ):
        raise CutoverError(f"{name} is not an approved production HTTPS origin")


def validate_odoo_resources(values: dict[str, str]) -> None:
    """Keep Odoo workers inside explicit memory and PostgreSQL budgets."""
    try:
        workers = int(values["ODOO_WORKERS"])
        cron_threads = int(values["USL_PRODUCTION_CRON_THREADS"])
        db_maxconn = int(values["ODOO_DB_MAXCONN"])
        memory_soft = int(values["ODOO_LIMIT_MEMORY_SOFT"])
        memory_hard = int(values["ODOO_LIMIT_MEMORY_HARD"])
        limit_request = int(values["ODOO_LIMIT_REQUEST"])
    except ValueError as error:
        raise CutoverError("Odoo resource policies must be integers") from error
    if not 1 <= workers <= 8:
        raise CutoverError("Odoo HTTP workers must be between 1 and 8")
    if not 1 <= cron_threads <= 4:
        raise CutoverError("production cron worker policy must be between 1 and 4")
    if not 4 <= db_maxconn <= 32:
        raise CutoverError("Odoo database pool must be between 4 and 32 per process")
    if (workers + cron_threads + 1) * db_maxconn > 80:
        raise CutoverError("Odoo worker pools can exceed PostgreSQL's connection budget")
    if not 512 * 1024**2 <= memory_soft < memory_hard <= 3 * 1024**3:
        raise CutoverError("Odoo worker memory limits are unsafe or inconsistent")
    if memory_hard - memory_soft < 128 * 1024**2:
        raise CutoverError("Odoo hard memory limit needs at least 128 MiB headroom")
    if not 1024 <= limit_request <= 32768:
        raise CutoverError("Odoo worker request recycling must be between 1024 and 32768")


def validate_environment(
    values: dict[str, str],
    candidate: dict | None = None,
    *,
    distribution_release: dict | None = None,
    phase: str = "configured",
) -> None:
    if (candidate is None) == (distribution_release is None):
        raise CutoverError("exactly one candidate or Distribution release is required")
    project = values["COMPOSE_PROJECT_NAME"]
    if not SAFE_PROJECT.fullmatch(project):
        raise CutoverError("unsafe production Compose project name")
    if values["USL_DEPLOYMENT_ENV"] != "production":
        raise CutoverError("USL_DEPLOYMENT_ENV must be production")
    if values["USL_OLLAMA_RUNTIME"] != "external":
        raise CutoverError("production must use the protected external Ollama runtime")
    expected_ollama = {
        "OLLAMA_IMAGE": "ollama/ollama:0.32.5@sha256:4dea9fb511947e24a84237bb636b0203abcb2ff0d3fbc7b4ff865deb91362131",
        "USL_EXTERNAL_OLLAMA_CONTAINER": "ollama",
        "PAPERLESS_AI_LLM_EMBEDDING_ENDPOINT": "http://ollama:11434",
        "PAPERLESS_AI_LLM_EMBEDDING_MODEL": "bge-m3:latest",
        "USL_OLLAMA_MANIFEST_SHA256": "7907646426070047a77226ac3e684fbbe8410524f7b4a74d02837e43f2146bab",
        "USL_OLLAMA_EMBEDDING_DIMENSION": "1024",
        "PAPERLESS_AI_LLM_EMBEDDING_BATCH_SIZE": "32",
    }
    mismatched_ollama = [
        name for name, expected in expected_ollama.items() if values.get(name) != expected
    ]
    if mismatched_ollama:
        raise CutoverError(
            "shared Ollama configuration differs from the qualified runtime: "
            + ", ".join(mismatched_ollama)
        )
    if values.get("USL_EINVOICE_LIVE_ENABLED", "") != "0" or values.get(
        "USL_EREPORTING_LIVE_ENABLED",
        "",
    ) != "0":
        raise CutoverError("regulatory live flags must remain 0")
    if values.get("ODOO_LIST_DB", "").lower() != "false":
        raise CutoverError("public Odoo database manager must be disabled")
    if values.get("ODOO_MAX_CRON_THREADS") != "0":
        raise CutoverError("staging Odoo cron must be paused")
    cron_policy = read_json(
        Path(__file__).resolve().parents[1] / "deploy/production.cron-policy.json"
    )
    try:
        cron_gates = json.loads(values["USL_PRODUCTION_CRON_GATES_JSON"])
    except json.JSONDecodeError as error:
        raise CutoverError("production cron gate decisions are invalid JSON") from error
    if (
        cron_policy.get("schema") != "usl-production-cron-policy-v1"
        or not isinstance(cron_gates, dict)
        or set(cron_gates) != set(cron_policy.get("gates") or [])
        or any(type(value) is not bool for value in cron_gates.values())
        or cron_gates.get("always") is not True
        or cron_gates.get("smtp") is not True
        or cron_gates.get("pdp") is not False
    ):
        raise CutoverError("production cron gate decisions are incomplete or unsafe")
    expected_smtp = {
        "ODOO_SMTP_SERVER": "smtp.resend.com",
        "ODOO_SMTP_PORT": "587",
        "ODOO_SMTP_SSL": "True",
        "ODOO_SMTP_USER": "resend",
        "ODOO_EMAIL_FROM": "odoo@unstaticlabs.com",
        "ODOO_FROM_FILTER": "unstaticlabs.com",
    }
    mismatched_smtp = [
        name for name, expected in expected_smtp.items() if values.get(name) != expected
    ]
    if mismatched_smtp or len(values.get("ODOO_SMTP_PASSWORD", "")) < 24:
        raise CutoverError(
            "host-level Resend configuration is missing or unsafe: "
            + ", ".join(mismatched_smtp or ["ODOO_SMTP_PASSWORD"])
        )
    validate_odoo_resources(values)
    database = values["ODOO_DB_NAME"]
    if (
        not SAFE_DATABASE.fullmatch(database)
        or database in {"postgres", "odoo", "odoo_dev", "odoo_online_source_saas_19_3"}
        or values.get("ODOO_INIT_DB") != database
        or values["ODOO_DB_FILTER"] != f"^{database}$"
    ):
        raise CutoverError("unsafe or inconsistent production database name/filter")
    if not IMAGE.fullmatch(values["ODOO_IMAGE"]):
        raise CutoverError("production Odoo image is not immutable")
    for name in ("PAPERLESS_IMAGE", "OLLAMA_IMAGE"):
        if not IMAGE.fullmatch(values[name]):
            raise CutoverError(f"production {name} is not immutable")
    for name in (
        "ODOO_MCP_IMAGE",
        "USL_DOCUMENT_RENDERER_IMAGE",
        "USL_SIGN_DSS_IMAGE",
        "USL_SIGN_STEP_CA_IMAGE",
    ):
        if not IMAGE.fullmatch(values[name]):
            raise CutoverError(f"production {name} is not immutable")

    if distribution_release is not None:
        try:
            release = validate_distribution_release(distribution_release)
        except ReleaseArtifactError as error:
            raise CutoverError(f"Distribution release was rejected: {error}") from error
        release_images = {
            "ODOO_IMAGE": release["image"]["digest_reference"],
            "PAPERLESS_IMAGE": release["paperless"]["digest_reference"],
            "USL_DOCUMENT_RENDERER_IMAGE": release["document_renderer"]["image"][
                "digest_reference"
            ],
            "USL_SIGN_DSS_IMAGE": release["sign_dss"]["digest_reference"],
            "ODOO_MCP_IMAGE": release["mcp"]["image_digest"],
        }
        mismatches = [
            name for name, expected in release_images.items() if values[name] != expected
        ]
        if mismatches:
            raise CutoverError(
                "production images differ from the Distribution release: "
                + ", ".join(mismatches)
            )
    else:
        candidate_identity = (candidate or {}).get("identity") or {}
        if values["ODOO_IMAGE"] != candidate_identity.get("image_digest"):
            raise CutoverError("production image differs from the approved candidate")
        for name, candidate_key in (
            ("PAPERLESS_IMAGE", "paperless_image_digest"),
            ("OLLAMA_IMAGE", "ollama_image_digest"),
        ):
            if values[name] != candidate_identity.get(candidate_key):
                raise CutoverError(f"production {name} differs from the approved candidate")
    mcp_release = load_release(Path(__file__).resolve().parents[1])
    if values["ODOO_MCP_RELEASE_COMMIT"] != mcp_release["commit"]:
        raise CutoverError("production Odoo MCP revision differs from the pinned release")
    if values["ODOO_MCP_IMAGE"] != mcp_release["image"]:
        raise CutoverError("production Odoo MCP image differs from the pinned release digest")
    if distribution_release is not None:
        release_mcp = distribution_release.get("mcp") or {}
        if (
            release_mcp.get("commit") != mcp_release["commit"]
            or release_mcp.get("image_digest") != mcp_release["image"]
        ):
            raise CutoverError("Distribution release differs from the pinned Odoo MCP release")
    if values["ODOO_MCP_ALLOW_LOCAL_HTTP_ODOO"].lower() != "false":
        raise CutoverError("production Odoo MCP must reject local HTTP Odoo targets")
    mcp_better_auth = Path(values["ODOO_MCP_BETTER_AUTH_SECRET_FILE"])
    mcp_encryption = Path(values["ODOO_MCP_CREDENTIAL_ENCRYPTION_KEY_FILE"])
    private_file(mcp_better_auth)
    private_file(mcp_encryption)
    if len(mcp_better_auth.read_text(encoding="utf-8").strip()) < 32:
        raise CutoverError("Odoo MCP Better Auth secret is too short")
    try:
        decoded_mcp_key = base64.b64decode(
            mcp_encryption.read_text(encoding="utf-8").strip(),
            validate=True,
        )
    except ValueError as error:
        raise CutoverError("Odoo MCP credential encryption key is invalid") from error
    if len(decoded_mcp_key) != 32:
        raise CutoverError("Odoo MCP credential encryption key must contain 32 bytes")
    if phase not in {"preflight", "configured"}:
        raise CutoverError("unsupported cutover validation phase")
    restored_sign_directories = {"USL_SIGN_STEP_CA_DIR", "USL_SIGN_DSS_SECRET_DIR"}
    for name, required_files in SIGN_SECRET_DIRECTORIES.items():
        path = Path(values[name])
        if phase == "preflight" and name in restored_sign_directories:
            if not path.is_absolute() or path.exists():
                raise CutoverError(f"production Sign restore destination must be fresh: {path}")
            parent = path.parent
            if not parent.is_dir() or parent.is_symlink() or stat.S_IMODE(parent.stat().st_mode) & 0o077:
                raise CutoverError(f"production Sign restore parent is unsafe: {parent}")
        else:
            private_directory(path, required_files)
    sign_evidence = Path(values["USL_SIGN_EVIDENCE_DIR"])
    if not sign_evidence.is_absolute():
        raise CutoverError("production Sign evidence path must be absolute")
    if phase == "preflight":
        if sign_evidence.exists():
            raise CutoverError("production Sign evidence restore destination must be fresh")
        if (
            not sign_evidence.parent.is_dir()
            or sign_evidence.parent.is_symlink()
            or stat.S_IMODE(sign_evidence.parent.stat().st_mode) & 0o077
        ):
            raise CutoverError("production Sign evidence restore parent is unsafe")
    elif not sign_evidence.is_dir() or sign_evidence.is_symlink():
        raise CutoverError("production Sign evidence restore is missing")
    key_ring = Path(values["USL_PERSONAL_AI_MASTER_KEYS_HOST_PATH"])
    if not key_ring.is_absolute():
        raise CutoverError("production Personal AI key ring path must be absolute")
    private_file(key_ring)
    for name in (
        "ODOO_PUBLIC_BASE_URL",
        "ODOO_MCP_PUBLIC_ORIGIN",
        "PAPERLESS_PUBLIC_URL",
        "POCKET_ID_APP_URL",
    ):
        _production_url(name, values[name])
    mcp_host = (urlsplit(values["ODOO_MCP_PUBLIC_ORIGIN"]).hostname or "").lower()
    mcp_allowed_hosts = {
        value.strip().lower()
        for value in values["ODOO_MCP_ALLOWED_HOSTS"].split(",")
        if value.strip()
    }
    mcp_allowed_origins = {
        value.strip().lower()
        for value in values["ODOO_MCP_ALLOWED_ORIGINS"].split(",")
        if value.strip()
    }
    if mcp_host not in mcp_allowed_hosts or {
        "localhost",
        "127.0.0.1",
        "::1",
    }.intersection(mcp_allowed_hosts | mcp_allowed_origins):
        raise CutoverError("production Odoo MCP host/origin allowlists are unsafe")
    if values["ODOO_MCP_HEALTHCHECK_HOST"].lower() not in mcp_allowed_hosts:
        raise CutoverError("Odoo MCP health-check host is not in the allowed-host policy")
    trusted_origins = {
        value.strip()
        for value in values["ODOO_MCP_OAUTH_TRUSTED_ORIGINS"].split(",")
        if value.strip()
    }
    if not trusted_origins:
        raise CutoverError("production Odoo MCP OAuth trusted origins are empty")
    for origin in trusted_origins:
        _production_url("ODOO_MCP_OAUTH_TRUSTED_ORIGINS", origin)
    if values.get("PAPERLESS_PUBLIC_BASE_URL") != values["PAPERLESS_PUBLIC_URL"]:
        raise CutoverError("Paperless callback base differs from its public URL")
    paperless_database = values["PAPERLESS_DB_NAME"]
    if (
        not SAFE_DATABASE.fullmatch(paperless_database)
        or paperless_database in {"postgres", database}
        or not SAFE_DATABASE.fullmatch(values["PAPERLESS_DB_USER"])
    ):
        raise CutoverError("unsafe or conflicting Paperless database identity")
    ports = {}
    for name in (
        "ODOO_HTTP_PORT",
        "ODOO_GEVENT_PORT",
        "ODOO_MCP_HTTP_PORT",
        "PAPERLESS_HTTP_PORT",
    ):
        try:
            port = int(values[name])
        except ValueError as error:
            raise CutoverError(f"{name} is not a valid loopback port") from error
        if not 1024 <= port <= 65535 or port in ports.values():
            raise CutoverError(f"{name} is unsafe or conflicts with another port")
        ports[name] = port
    paperless_host = (urlsplit(values["PAPERLESS_PUBLIC_URL"]).hostname or "").lower()
    allowed_hosts = {
        value.strip().lower()
        for value in values["PAPERLESS_ALLOWED_HOSTS"].split(",")
        if value.strip()
    }
    if (
        paperless_host not in allowed_hosts
        or {"localhost", "127.0.0.1", "::1"}.intersection(allowed_hosts)
    ):
        raise CutoverError("Paperless allowed hosts are unsafe or incomplete")
    if values["POCKET_ID_CLIENT_ID"] == values["POCKET_ID_PAPERLESS_CLIENT_ID"]:
        raise CutoverError("Odoo and Paperless require separate Pocket ID clients")
    if values["USL_EXTERNAL_IDENTITY_NETWORK"] == values["USL_EXTERNAL_INGRESS_NETWORK"]:
        raise CutoverError("identity and ingress networks must be explicit and distinct")
    if len({
        values["USL_EXTERNAL_IDENTITY_NETWORK"],
        values["USL_EXTERNAL_INGRESS_NETWORK"],
        values["USL_EXTERNAL_OLLAMA_NETWORK"],
    }) != 3:
        raise CutoverError("identity, ingress and shared Ollama networks must be distinct")
    if any(
        not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]+", values[name])
        for name in (
            "USL_EXTERNAL_IDENTITY_NETWORK",
            "USL_EXTERNAL_INGRESS_NETWORK",
            "USL_EXTERNAL_OLLAMA_NETWORK",
        )
    ):
        raise CutoverError("external network name is unsafe")
    defaults = {"odoo", "admin", "password", "paperless-change-me-dev-only"}
    secret_minimums = {
        "ODOO_ADMIN_PASSWORD": 24,
        "ODOO_DB_PASSWORD": 24,
        "PAPERLESS_DB_PASSWORD": 24,
        "PAPERLESS_SECRET_KEY": 48,
        "POCKET_ID_CLIENT_SECRET": 24,
        "POCKET_ID_PAPERLESS_CLIENT_SECRET": 24,
        "USL_POCKET_ID_BREAK_GLASS_PASSWORD": 24,
    }
    for name, minimum in secret_minimums.items():
        value = values[name]
        if len(value) < minimum or value.lower() in defaults or "CHANGE_ME" in value:
            raise CutoverError(f"{name} is default, placeholder or too short")
    if values["ODOO_DB_PASSWORD"] != values.get("POSTGRES_PASSWORD"):
        raise CutoverError("Odoo and PostgreSQL database credentials differ")
    volume_names = [values[key] for key in VOLUME_KEYS]
    if len(set(volume_names)) != len(volume_names):
        raise CutoverError("production application volumes must be distinct")
    for key in VOLUME_KEYS:
        name = values[key]
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]+", name):
            raise CutoverError(f"{key} is not a safe explicit volume name")


def validate_compose(config: dict, values: dict[str, str]) -> None:
    services = config.get("services") or {}
    for name, service in services.items():
        image = str(service.get("image") or "").lower()
        if (
            "pocket-id" in name.lower()
            or "pocket_id" in name.lower()
            or "pocket-id" in image
        ):
            raise CutoverError("Compose topology contains a managed Pocket ID service")
        dependencies = service.get("depends_on") or {}
        if any("pocket" in dependency.lower() for dependency in dependencies):
            raise CutoverError("Compose topology depends on a managed Pocket ID service")
    for name in ("odoo", "paperless-webserver"):
        if name not in services:
            raise CutoverError(f"Compose topology is missing {name}")
        ports = services[name].get("ports") or []
        if not ports:
            raise CutoverError(f"{name} has no loopback staging port")
        for port in ports:
            host_ip = port.get("host_ip") if isinstance(port, dict) else None
            if host_ip not in {"127.0.0.1", "::1"}:
                raise CutoverError(f"{name} staging port is not loopback-only")
        service_networks = set((services[name].get("networks") or {}).keys())
        if not {"external-identity", "external-ingress"}.issubset(
            service_networks,
        ):
            raise CutoverError(f"{name} is not joined to approved external networks")
    mcp_service = services.get("odoo-mcp")
    if not mcp_service:
        raise CutoverError("Compose topology is missing odoo-mcp")
    mcp_ports = mcp_service.get("ports") or []
    if not mcp_ports or any(
        not isinstance(port, dict) or port.get("host_ip") not in {"127.0.0.1", "::1"}
        for port in mcp_ports
    ):
        raise CutoverError("odoo-mcp staging port is not loopback-only")
    if not {"default", "external-ingress"}.issubset(
        set((mcp_service.get("networks") or {}).keys()),
    ):
        raise CutoverError("odoo-mcp is not joined to the runtime and ingress networks")
    mcp_mounts = mcp_service.get("volumes") or []
    if not any(
        isinstance(mount, dict)
        and mount.get("type") == "volume"
        and mount.get("target") == "/data"
        and mount.get("source") == "odoo-mcp-oauth-data"
        for mount in mcp_mounts
    ):
        raise CutoverError("odoo-mcp does not persist its OAuth state in /data")
    mcp_secrets = {
        item.get("source") if isinstance(item, dict) else item
        for item in (mcp_service.get("secrets") or [])
    }
    if not {
        "odoo_mcp_better_auth_secret",
        "odoo_mcp_credential_encryption_key",
    }.issubset(mcp_secrets):
        raise CutoverError("odoo-mcp does not mount both required Docker secrets")
    renderer_cert_dir = Path(values["USL_DOCUMENT_RENDERER_CERT_DIR"])
    required_odoo_secrets = {
        "/run/secrets/document-renderer/ca.crt": str(renderer_cert_dir / "ca.crt"),
        "/run/secrets/document-renderer/odoo.crt": str(renderer_cert_dir / "odoo.crt"),
        "/run/secrets/document-renderer/odoo.key": str(renderer_cert_dir / "odoo.key"),
        "/run/usl-sign": values["USL_SIGN_ODOO_SECRET_DIR"],
    }
    for service_name in ("odoo", "init-db"):
        service = services.get(service_name)
        if not service:
            raise CutoverError(f"Compose topology is missing {service_name}")
        mounts = service.get("volumes") or []
        for target, source in required_odoo_secrets.items():
            if not any(
                isinstance(mount, dict)
                and mount.get("type") == "bind"
                and mount.get("source") == source
                and mount.get("target") == target
                and mount.get("read_only") is True
                for mount in mounts
            ):
                raise CutoverError(
                    f"{service_name} does not mount {target} from its approved "
                    "secret source read-only",
                )
    renderer_service = services.get("usl-document-renderer")
    if not renderer_service:
        raise CutoverError("Compose topology is missing usl-document-renderer")
    renderer_mounts = renderer_service.get("volumes") or []
    for filename in ("ca.crt", "renderer.crt", "renderer.key"):
        source = str(renderer_cert_dir / filename)
        target = f"/run/secrets/document-renderer/{filename}"
        if not any(
            isinstance(mount, dict)
            and mount.get("type") == "bind"
            and mount.get("source") == source
            and mount.get("target") == target
            and mount.get("read_only") is True
            for mount in renderer_mounts
        ):
            raise CutoverError(
                "usl-document-renderer does not mount "
                f"{target} from its approved secret source read-only",
            )
    expected_images = {
        "odoo": values["ODOO_IMAGE"],
        "odoo-mcp": values["ODOO_MCP_IMAGE"],
        "odoo-mcp-oauth-init": values["ODOO_MCP_IMAGE"],
        "paperless-webserver": values["PAPERLESS_IMAGE"],
        "usl-document-renderer": values["USL_DOCUMENT_RENDERER_IMAGE"],
        "usl-sign-dss": values["USL_SIGN_DSS_IMAGE"],
        "usl-sign-step-ca": values["USL_SIGN_STEP_CA_IMAGE"],
    }
    if "paperless-ollama" in services or "paperless-model-init" in services:
        raise CutoverError("production topology must not own an Ollama service")
    model_preflight = services.get("paperless-model-preflight") or {}
    if "external-ollama" not in set((model_preflight.get("networks") or {}).keys()):
        raise CutoverError("Ollama model preflight is not joined to the shared network")
    for service_name, expected_image in expected_images.items():
        if (services.get(service_name) or {}).get("image") != expected_image:
            raise CutoverError(
                f"{service_name} does not use its candidate-bound image",
            )
    networks = config.get("networks") or {}
    expected_networks = {
        values["USL_EXTERNAL_IDENTITY_NETWORK"],
        values["USL_EXTERNAL_INGRESS_NETWORK"],
        values["USL_EXTERNAL_OLLAMA_NETWORK"],
    }
    actual_external = {
        item.get("name")
        for item in networks.values()
        if item.get("external") is True
    }
    if not expected_networks.issubset(actual_external):
        raise CutoverError("Compose topology does not use the approved external networks")
    configured_volumes = config.get("volumes") or {}
    volume_names = {item.get("name") for item in configured_volumes.values()}
    if not {values[key] for key in VOLUME_KEYS}.issubset(volume_names):
        raise CutoverError("Compose topology does not use every dedicated application volume")
    project = values["COMPOSE_PROJECT_NAME"]
    for item in configured_volumes.values():
        if item.get("name") in {values[key] for key in VOLUME_KEYS} and (
            item.get("external") is True
            or (item.get("labels") or {}).get(
                "com.unstaticlabs.migration.project",
            )
            != project
        ):
            raise CutoverError("Compose topology contains an unowned application volume")


def validate_volume_state(
    volume_state: list[dict],
    values: dict[str, str],
    *,
    require_empty: bool,
) -> None:
    expected = {values[key] for key in VOLUME_KEYS}
    observed = {item.get("name") for item in volume_state}
    unexpected = observed - expected
    if unexpected:
        raise CutoverError("foreign volumes were included: " + ", ".join(sorted(unexpected)))
    project = values["COMPOSE_PROJECT_NAME"]
    for item in volume_state:
        labels = item.get("labels") or {}
        if labels.get("com.unstaticlabs.migration.project") != project:
            raise CutoverError(f"foreign/unowned target volume exists: {item.get('name')}")
        if require_empty and int(item.get("file_count", 0)):
            raise CutoverError(f"target application volume is not empty: {item.get('name')}")


def validate_policy(path: Path, fingerprint: str) -> dict:
    private_file(path)
    policy = read_json(path)
    if policy.get("schema") != POLICY_SCHEMA:
        raise CutoverError("external identity policy schema is unsupported")
    if policy.get("candidate_fingerprint") != fingerprint:
        raise CutoverError("external identity policy refers to another candidate")
    for name in ("odoo_users", "paperless_identities"):
        if not isinstance(policy.get(name), list) or not policy[name]:
            raise CutoverError(f"external identity policy {name} is empty")
    required_paperless = {"subject", "username", "email", "display_name"}
    if any(set(item) != required_paperless for item in policy["paperless_identities"]):
        raise CutoverError("Paperless identity policy entry is incomplete")
    cron_allowlist = policy.get("approved_cron_xmlids")
    if not isinstance(cron_allowlist, list) or any(
        not isinstance(value, str) or "." not in value
        for value in cron_allowlist
    ):
        raise CutoverError("approved_cron_xmlids must be an explicit XML-ID list")
    if policy.get("outbound_integrations_enabled") is not False:
        raise CutoverError("initial outbound integrations must remain disabled")
    return policy


def validate_journeys(path: Path, fingerprint: str) -> dict:
    private_file(path)
    evidence = read_json(path)
    if evidence.get("schema") != JOURNEY_SCHEMA:
        raise CutoverError("browser journey evidence schema is unsupported")
    if evidence.get("candidate_fingerprint") != fingerprint:
        raise CutoverError("browser journey evidence refers to another candidate")
    if evidence.get("status") != "passed":
        raise CutoverError("browser journey evidence did not pass")
    before = evidence.get("external_pocket_state_before_sha256")
    after = evidence.get("external_pocket_state_after_sha256")
    if not isinstance(before, str) or not FINGERPRINT.fullmatch(before) or before != after:
        raise CutoverError("external Pocket ID state changed during rehearsal")
    required = {
        "odoo_administrator",
        "odoo_collaborator",
        "accounting_read_only",
        "multi_company_isolation",
        "odoo_mcp_oauth",
        "paperless_documents",
    }
    journeys = evidence.get("journeys") or []
    passed = {
        item.get("name")
        for item in journeys
        if isinstance(item, dict) and item.get("status") == "passed"
    }
    missing = sorted(required - passed)
    if missing:
        raise CutoverError("required browser journeys are missing: " + ", ".join(missing))
    return evidence


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        path.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)


def transition(path: Path, fingerprint: str, action: str) -> dict:
    if path.exists():
        private_file(path)
        state = read_json(path)
    else:
        state = {
            "schema": STATE_SCHEMA,
            "candidate_fingerprint": fingerprint,
            "status": "new",
            "reset_allowed": True,
            "history": [],
        }
    if state.get("schema") != STATE_SCHEMA or state.get(
        "candidate_fingerprint",
    ) != fingerprint:
        raise CutoverError("cut-over state belongs to another candidate")
    current = state.get("status")
    expected = {
        "preflight": {"new", "preflight"},
        "stage": {"preflight"},
        "configure": {"staged", "configured"},
        "gate": {"configured", "gated"},
        "admit": {"gated"},
        "reset": {"preflight", "staged", "configured", "gated"},
    }
    if action not in expected or current not in expected[action]:
        raise CutoverError(f"cannot {action} candidate in {current!r} state")
    if action == "reset" and not state.get("reset_allowed"):
        raise CutoverError("candidate reset is permanently disabled")
    target = {
        "preflight": "preflight",
        "stage": "staged",
        "configure": "configured",
        "gate": "gated",
        "admit": "admitted",
        "reset": "preflight",
    }[action]
    state["status"] = target
    state["history"].append(action)
    if action == "admit":
        state["reset_allowed"] = False
    atomic_json(path, state)
    return state


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    subparsers = result.add_subparsers(dest="command", required=True)
    env_parser = subparsers.add_parser("env")
    env_parser.add_argument("--env-file", type=Path, required=True)
    artifact_group = env_parser.add_mutually_exclusive_group(required=True)
    artifact_group.add_argument("--candidate-manifest", type=Path)
    artifact_group.add_argument("--distribution-release", type=Path)
    env_parser.add_argument("--compose-config", type=Path)
    env_parser.add_argument("--volume-state", type=Path)
    env_parser.add_argument("--field")
    env_parser.add_argument("--field-only", action="store_true")
    env_parser.add_argument("--phase", choices=("preflight", "configured"), default="configured")
    policy_parser = subparsers.add_parser("policy")
    policy_parser.add_argument("--policy", type=Path, required=True)
    policy_parser.add_argument("--fingerprint", required=True)
    policy_parser.add_argument(
        "--field",
        choices=("odoo_users", "paperless_identities", "approved_cron_xmlids"),
    )
    journey_parser = subparsers.add_parser("journeys")
    journey_parser.add_argument("--evidence", type=Path, required=True)
    journey_parser.add_argument("--fingerprint", required=True)
    state_parser = subparsers.add_parser("state")
    state_parser.add_argument("--state", type=Path, required=True)
    state_parser.add_argument("--fingerprint", required=True)
    state_parser.add_argument(
        "--action",
        required=True,
        choices=("preflight", "stage", "configure", "gate", "admit", "reset"),
    )
    return result


def main() -> None:
    args = parser().parse_args()
    try:
        if args.command == "env":
            values = parse_env(args.env_file.resolve())
            if args.field_only:
                if not args.field or args.field not in values:
                    raise CutoverError("--field-only requires a known environment field")
                print(values[args.field])
                return
            if args.distribution_release:
                release = read_json(args.distribution_release.resolve())
                validate_environment(
                    values,
                    distribution_release=release,
                    phase=args.phase,
                )
            else:
                candidate = read_json(args.candidate_manifest.resolve())
                validate_environment(values, candidate, phase=args.phase)
            if args.compose_config:
                validate_compose(read_json(args.compose_config.resolve()), values)
            if args.volume_state:
                state = read_json(args.volume_state.resolve()).get("volumes") or []
                validate_volume_state(state, values, require_empty=True)
            if args.field:
                if args.field not in values:
                    raise CutoverError(f"unknown environment field: {args.field}")
                print(values[args.field])
            else:
                print(json.dumps({"status": "passed", "project": values["COMPOSE_PROJECT_NAME"]}, sort_keys=True))
        elif args.command == "policy":
            policy = validate_policy(args.policy.resolve(), args.fingerprint)
            print(json.dumps(policy[args.field] if args.field else policy, separators=(",", ":"), sort_keys=True))
        elif args.command == "journeys":
            print(json.dumps(
                validate_journeys(args.evidence.resolve(), args.fingerprint),
                separators=(",", ":"),
                sort_keys=True,
            ))
        else:
            if not FINGERPRINT.fullmatch(args.fingerprint):
                raise CutoverError("candidate fingerprint is invalid")
            state = transition(args.state.resolve(), args.fingerprint, args.action)
            print(json.dumps(state, indent=2, sort_keys=True))
    except (CutoverError, OSError, ValueError, json.JSONDecodeError) as error:
        raise SystemExit(f"Production cut-over rejected: {error}") from error


if __name__ == "__main__":
    main()
