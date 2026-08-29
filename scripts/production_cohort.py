#!/usr/bin/env python3
"""Validate coordinated production cohort manifests outside the Odoo database."""

# ruff: noqa: EM101, T201 - operator CLI reports concise literal failures.

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from continuous_operations_contracts import (  # noqa: E402
    ARTIFACT_ROLES,
    COHORT_SCHEMA,
    COHORT_UNITS,
    ContractError,
    exact_keys,
    validate_commit,
    validate_digest_reference,
    validate_identifier,
    validate_sha256,
    validate_timestamp,
    verify_checksum,
)


class CohortError(ContractError):
    """A production cohort is incomplete or cannot prove restore parity."""


def _unit(value: object, context: str) -> dict[str, Any]:
    try:
        unit = exact_keys(value, {"snapshot_id", "sha256", "size_bytes"}, context)
        validate_identifier(unit["snapshot_id"], f"{context}.snapshot_id")
        validate_sha256(unit["sha256"], f"{context}.sha256")
    except ContractError as error:
        raise CohortError(str(error)) from error
    if not isinstance(unit["size_bytes"], int) or unit["size_bytes"] < 0:
        raise CohortError(f"{context}.size_bytes must be a non-negative integer")
    return unit


def validate(payload: object) -> dict[str, Any]:
    try:
        root = exact_keys(
            payload,
            {
                "schema",
                "cohort_id",
                "created_at",
                "release",
                "storage",
                "models",
                "queues",
                "restore_evidence",
                "secrets",
                "contract_sha256",
            },
            "cohort",
        )
    except ContractError as error:
        raise CohortError(str(error)) from error
    if root["schema"] != COHORT_SCHEMA:
        raise CohortError(f"unsupported schema: {root['schema']!r}")
    try:
        validate_identifier(root["cohort_id"], "cohort.cohort_id")
        validate_timestamp(root["created_at"], "cohort.created_at")
        verify_checksum(root, "cohort")
        release = exact_keys(
            root["release"],
            {"source_commit_sha", "release_contract_sha256", "artifacts"},
            "cohort.release",
        )
        validate_commit(
            release["source_commit_sha"], "cohort.release.source_commit_sha",
        )
        validate_sha256(
            release["release_contract_sha256"], "cohort.release.release_contract_sha256",
        )
        artifacts = exact_keys(
            release["artifacts"], set(ARTIFACT_ROLES), "cohort.release.artifacts",
        )
    except ContractError as error:
        raise CohortError(str(error)) from error
    for role, reference in artifacts.items():
        try:
            validate_digest_reference(reference, f"cohort.release.artifacts.{role}")
        except ContractError as error:
            raise CohortError(str(error)) from error

    try:
        storage = exact_keys(root["storage"], set(COHORT_UNITS), "cohort.storage")
    except ContractError as error:
        raise CohortError(str(error)) from error
    for name in COHORT_UNITS:
        _unit(storage[name], f"cohort.storage.{name}")

    if not isinstance(root["models"], list) or not root["models"]:
        raise CohortError(
            "cohort.models must contain the exact Ollama/BGE model identities",
        )
    model_names: set[str] = set()
    for index, value in enumerate(root["models"]):
        try:
            model = exact_keys(
                value, {"name", "digest", "archive_sha256"}, f"cohort.models[{index}]",
            )
            validate_sha256(
                model["archive_sha256"], f"cohort.models[{index}].archive_sha256",
            )
        except ContractError as error:
            raise CohortError(str(error)) from error
        if (
            not isinstance(model["name"], str)
            or not model["name"]
            or model["name"] in model_names
        ):
            raise CohortError("cohort.models names must be non-empty and unique")
        if not isinstance(model["digest"], str) or not model["digest"].startswith(
            "sha256:",
        ):
            raise CohortError(
                f"cohort.models[{index}].digest must be a sha256 model digest",
            )
        validate_sha256(
            model["digest"].removeprefix("sha256:"), f"cohort.models[{index}].digest",
        )
        model_names.add(model["name"])
    if not any("bge" in name.lower() for name in model_names):
        raise CohortError(
            "cohort.models must include the qualified BGE embedding model",
        )

    try:
        queues = exact_keys(
            root["queues"], {"odoo_jobs", "paperless_broker"}, "cohort.queues",
        )
    except ContractError as error:
        raise CohortError(str(error)) from error
    for name, queue in queues.items():
        try:
            queue_value = exact_keys(
                queue, {"state", "pending", "authoritative"}, f"cohort.queues.{name}",
            )
        except ContractError as error:
            raise CohortError(str(error)) from error
        if queue_value != {"state": "drained", "pending": 0, "authoritative": False}:
            raise CohortError(
                f"cohort.queues.{name} must be drained and explicitly non-authoritative",
            )

    if not isinstance(root["restore_evidence"], list):
        raise CohortError("cohort.restore_evidence must be a list")
    restored: set[str] = set()
    for index, value in enumerate(root["restore_evidence"]):
        try:
            evidence = exact_keys(
                value,
                {
                    "component",
                    "environment",
                    "restored_at",
                    "verification_sha256",
                    "status",
                },
                f"cohort.restore_evidence[{index}]",
            )
            validate_timestamp(
                evidence["restored_at"], f"cohort.restore_evidence[{index}].restored_at",
            )
            validate_sha256(
                evidence["verification_sha256"],
                f"cohort.restore_evidence[{index}].verification_sha256",
            )
        except ContractError as error:
            raise CohortError(str(error)) from error
        if (
            evidence["component"] not in COHORT_UNITS
            or evidence["component"] in restored
        ):
            raise CohortError(
                "restore evidence components must be canonical and unique",
            )
        if (
            evidence["environment"] != "fresh_isolated_volumes"
            or evidence["status"] != "verified"
        ):
            raise CohortError(
                "every cohort unit requires a verified fresh-volume restore",
            )
        restored.add(evidence["component"])
    if restored != set(COHORT_UNITS):
        raise CohortError(
            f"restore evidence is incomplete (missing={sorted(set(COHORT_UNITS) - restored)})",
        )

    try:
        secrets = exact_keys(root["secrets"], {"provider", "copied"}, "cohort.secrets")
    except ContractError as error:
        raise CohortError(str(error)) from error
    if secrets != {"provider": "infisical", "copied": False}:
        raise CohortError(
            "Infisical secrets must be referenced at runtime and never copied",
        )
    return root


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path")
    arguments = parser.parse_args()
    try:
        payload = json.loads(Path(arguments.path).read_text(encoding="utf-8"))
        validate(payload)
    except (OSError, json.JSONDecodeError, CohortError) as error:
        print(f"production cohort: {error}", file=sys.stderr)
        return 2
    print(f"Valid {COHORT_SCHEMA}: {arguments.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
