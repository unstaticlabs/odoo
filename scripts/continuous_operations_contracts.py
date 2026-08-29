"""Strict contracts shared by permanent USL continuous operations tooling."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any

COMMIT = re.compile(r"[0-9a-f]{40}")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
DIGEST_REFERENCE = re.compile(r"[a-z0-9][a-z0-9._/-]*@sha256:[0-9a-f]{64}")
SHA256 = re.compile(r"[0-9a-f]{64}")
IDENTIFIER = re.compile(r"[a-z0-9][a-z0-9._:-]{2,127}")

RELEASE_SCHEMA = "usl-distribution-release/v3"
COHORT_SCHEMA = "usl-production-cohort/v1"
RUN_SCHEMA = "usl-deployment-run/v1"
UPGRADE_SCHEMA = "usl-upgrade-plan/v1"
ARTIFACT_BUILD_PLAN_SCHEMA = "usl-artifact-build-plan/v1"

ARTIFACT_ROLES = (
    "odoo_distribution",
    "operations_tool",
    "paperless_overlay",
    "document_renderer",
    "native_sign_dss",
)

COHORT_UNITS = (
    "odoo_postgresql",
    "odoo_filestore",
    "paperless_postgresql",
    "paperless_media",
    "paperless_data",
    "paperless_search",
    "paperless_vector",
    "paperless_export",
    "paperless_trash",
    "ollama_models",
    "native_sign_step_ca",
    "native_sign_evidence",
)

STAGES = (
    "validate",
    "drain",
    "quiesce",
    "snapshot",
    "restore",
    "rehearse_upgrade",
    "qualify",
    "prepare_pins",
    "upgrade_production",
    "admit",
    "reopen",
    "record",
)


class ContractError(ValueError):
    """A contract is incomplete, ambiguous, or internally inconsistent."""


def exact_keys(value: object, expected: set[str], context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(f"{context} must be an object")
    actual = set(value)
    if actual != expected:
        raise ContractError(
            f"{context} keys differ "
            f"(missing={sorted(expected - actual)}, extra={sorted(actual - expected)})",
        )
    return value


def canonical_sha256(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def validate_sha256(value: object, context: str) -> str:
    if not isinstance(value, str) or not SHA256.fullmatch(value):
        raise ContractError(f"{context} must be 64 lowercase hexadecimal characters")
    return value


def validate_commit(value: object, context: str) -> str:
    if not isinstance(value, str) or not COMMIT.fullmatch(value):
        raise ContractError(f"{context} must be a full lowercase Git SHA")
    return value


def validate_digest_reference(value: object, context: str) -> str:
    if not isinstance(value, str) or not DIGEST_REFERENCE.fullmatch(value):
        raise ContractError(f"{context} must be an immutable sha256 OCI reference")
    return value


def validate_timestamp(value: object, context: str) -> str:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ContractError(f"{context} must be an RFC 3339 UTC timestamp")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ContractError(f"{context} must be an RFC 3339 UTC timestamp") from error
    return value


def validate_identifier(value: object, context: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ContractError(f"{context} must be a stable lowercase identifier")
    return value


def with_checksum(payload: dict[str, Any]) -> dict[str, Any]:
    unsigned = {
        key: value for key, value in payload.items() if key != "contract_sha256"
    }
    return {**unsigned, "contract_sha256": canonical_sha256(unsigned)}


def verify_checksum(payload: dict[str, Any], context: str) -> None:
    supplied = validate_sha256(
        payload.get("contract_sha256"), f"{context}.contract_sha256",
    )
    unsigned = {
        key: value for key, value in payload.items() if key != "contract_sha256"
    }
    expected = canonical_sha256(unsigned)
    if supplied != expected:
        raise ContractError(f"{context}.contract_sha256 does not match the contract")
