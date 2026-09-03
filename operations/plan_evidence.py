"""Sign and verify the exact module plan qualified by staging."""

from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from operations.module_release import validate_upgrade_plan


SCHEMA = "usl-staging-upgrade-plan-evidence/v1"


class PlanEvidenceError(ValueError):
    pass


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _openssl(arguments: list[str], *, data: bytes | None = None, label: str) -> bytes:
    try:
        result = subprocess.run(["openssl", *arguments], input=data, capture_output=True, check=False)
    except OSError as error:
        raise PlanEvidenceError("OpenSSL is unavailable") from error
    if result.returncode:
        raise PlanEvidenceError(label)
    return result.stdout


def _public_from_private(path: Path) -> bytes:
    return _openssl(["pkey", "-in", str(path), "-pubout", "-outform", "DER"], label="staging plan signing key is invalid")


def _public_identity(path: Path) -> str:
    value = _openssl(["pkey", "-pubin", "-in", str(path), "-outform", "DER"], label="staging plan verification key is invalid")
    return hashlib.sha256(value).hexdigest()


def sign(
    plan: dict[str, Any],
    private_key: Path,
    *,
    snapshot: str,
    generation: str,
    health: dict[str, Any],
    smoke: dict[str, Any],
) -> dict[str, Any]:
    plan = validate_upgrade_plan(plan)
    public_der = _public_from_private(private_key)
    body = {
        "schema": SCHEMA,
        "plan": plan,
        "staging": {
            "target": "staging",
            "snapshot": snapshot,
            "generation": generation,
            "candidate_release": plan["candidate_release"],
            "health_sha256": _digest(health),
            "smoke_sha256": _digest(smoke),
            "status": "passed",
        },
        "signed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    with tempfile.NamedTemporaryFile() as body_file:
        body_file.write(_canonical(body))
        body_file.flush()
        signature_value = _openssl(
            ["pkeyutl", "-sign", "-rawin", "-inkey", str(private_key), "-in", body_file.name],
            label="cannot sign staging upgrade plan",
        )
    return {
        **body,
        "signature": {
            "algorithm": "ed25519",
            "public_key_sha256": hashlib.sha256(public_der).hexdigest(),
            "value": base64.b64encode(signature_value).decode(),
        },
    }


def verify(value: object, public_key: Path) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"schema", "plan", "staging", "signed_at", "signature"}:
        raise PlanEvidenceError("staging plan evidence fields differ")
    if value.get("schema") != SCHEMA:
        raise PlanEvidenceError("staging plan evidence schema differs")
    plan = validate_upgrade_plan(value["plan"])
    staging = value.get("staging")
    if not isinstance(staging, dict) or set(staging) != {
        "target", "snapshot", "generation", "candidate_release", "health_sha256", "smoke_sha256", "status"
    }:
        raise PlanEvidenceError("staging qualification evidence fields differ")
    if staging.get("target") != "staging" or staging.get("status") != "passed":
        raise PlanEvidenceError("upgrade plan was not admitted by staging")
    if staging.get("candidate_release") != plan["candidate_release"]:
        raise PlanEvidenceError("staging evidence targets another release")
    for field in ("snapshot", "health_sha256", "smoke_sha256"):
        item = staging.get(field)
        if not isinstance(item, str) or len(item) != 64:
            raise PlanEvidenceError(f"staging evidence {field} is invalid")
    if not isinstance(staging.get("generation"), str) or not staging["generation"].startswith("g"):
        raise PlanEvidenceError("staging evidence generation is invalid")
    signature = value.get("signature")
    if not isinstance(signature, dict) or set(signature) != {"algorithm", "public_key_sha256", "value"}:
        raise PlanEvidenceError("staging plan signature fields differ")
    if signature.get("algorithm") != "ed25519":
        raise PlanEvidenceError("staging plan signature algorithm differs")
    if signature.get("public_key_sha256") != _public_identity(public_key):
        raise PlanEvidenceError("staging plan signing key identity differs")
    body = {key: item for key, item in value.items() if key != "signature"}
    try:
        signature_value = base64.b64decode(signature["value"], validate=True)
    except (ValueError, TypeError) as error:
        raise PlanEvidenceError("staging plan signature is malformed") from error
    with tempfile.NamedTemporaryFile() as body_file, tempfile.NamedTemporaryFile() as signature_file:
        body_file.write(_canonical(body))
        body_file.flush()
        signature_file.write(signature_value)
        signature_file.flush()
        _openssl(
            ["pkeyutl", "-verify", "-rawin", "-pubin", "-inkey", str(public_key), "-in", body_file.name, "-sigfile", signature_file.name],
            label="staging plan signature is invalid",
        )
    return plan
