#!/usr/bin/env python3
"""Validate and advance the idempotent USL continuous-deployment state machine."""

# ruff: noqa: EM101, T201, TRY301 - fail-closed operator CLI uses literal errors.

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import urllib.request
from datetime import UTC, datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import distribution_release  # noqa: E402
import production_cohort  # noqa: E402
from continuous_operations_contracts import (  # noqa: E402
    ARTIFACT_ROLES,
    RUN_SCHEMA,
    STAGES,
    ContractError,
    canonical_sha256,
    exact_keys,
    validate_commit,
    validate_digest_reference,
    validate_identifier,
    validate_sha256,
    validate_timestamp,
    verify_checksum,
    with_checksum,
)

PARIS = ZoneInfo("Europe/Paris")
RELEASE_ONLY_STAGES = {
    "rehearse_upgrade",
    "qualify",
    "prepare_pins",
    "upgrade_production",
    "admit",
}
MUTATION_STAGE = "upgrade_production"
RECOVERY_HOOKS = {
    "pre_mutation_verify",
    "pre_mutation_reopen",
    "rollback_restore_cohort",
    "rollback_restore_pins",
    "rollback_verify",
    "rollback_reopen",
}
RUN_STATES = {
    "planned",
    "deferred",
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
    "recorded",
    "failed_pre_mutation",
    "rolled_back",
    "incident_required",
}


class DeploymentRunError(ContractError):
    """The run cannot safely advance."""


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def parse_now(value: str | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise DeploymentRunError("--now must include a timezone")
    return parsed.astimezone(UTC)


def service_run_id(now: datetime) -> str:
    """Return the schedule-safe deterministic identity for the Paris service day."""
    return f"usl-continuous-{now.astimezone(PARIS).date():%Y%m%d}"


def atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = with_checksum(payload)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False,
    ) as stream:
        json.dump(payload, stream, indent=2)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, path)


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise DeploymentRunError(f"{path} must contain an object")
    return value


def pins(release: dict[str, Any]) -> dict[str, str]:
    return {
        role: release["artifacts"][role]["digest_reference"] for role in ARTIFACT_ROLES
    }


def _stage_record(name: str, status: str = "pending") -> dict[str, Any]:
    return {
        "name": name,
        "status": status,
        "attempts": 0,
        "started_at": None,
        "completed_at": None,
        "evidence_sha256": None,
        "error": None,
    }


def initialize(
    *,
    run_id: str | None,
    mode: str,
    deployed_release_path: Path,
    candidate_release_path: Path | None,
    cohort_path: Path,
    now: datetime,
    candidate_prior_release_path: Path | None = None,
) -> dict[str, Any]:
    if mode not in {"release", "backup_only", "auto"}:
        raise DeploymentRunError("mode must be release, backup_only, or auto")
    deployed = distribution_release.validate(
        load_json(deployed_release_path), historical=True,
    )
    candidate = deployed
    if mode == "auto":
        mode = (
            "release"
            if candidate_release_path and candidate_release_path.is_file()
            else "backup_only"
        )
    if mode == "release":
        if candidate_release_path is None:
            raise DeploymentRunError("release mode requires --candidate-release")
        candidate_prior = (
            load_json(candidate_prior_release_path)
            if candidate_prior_release_path and candidate_prior_release_path.is_file()
            else None
        )
        candidate = distribution_release.validate(
            load_json(candidate_release_path), prior_release=candidate_prior,
        )
    cohort = production_cohort.validate(load_json(cohort_path))
    deployed_sha = deployed["contract_sha256"]
    candidate_sha = candidate["contract_sha256"]
    if cohort["release"]["release_contract_sha256"] != deployed_sha:
        raise DeploymentRunError(
            "the recovery cohort does not identify the deployed release",
        )
    if cohort["release"]["source_commit_sha"] != deployed["source"]["commit_sha"]:
        raise DeploymentRunError(
            "the recovery cohort source SHA differs from the deployed release",
        )
    local = now.astimezone(PARIS)
    run_id = run_id or service_run_id(now)
    validate_identifier(run_id, "run_id")
    state = "planned"
    if mode == "release" and (local.hour, local.minute) > (3, 45):
        state = "deferred"
    stages = []
    for name in STAGES:
        status = (
            "skipped"
            if mode == "backup_only" and name in RELEASE_ONLY_STAGES
            else "pending"
        )
        stages.append(_stage_record(name, status))
    timestamp = now.isoformat().replace("+00:00", "Z")
    value = {
        "schema": RUN_SCHEMA,
        "run_id": run_id,
        "mode": mode,
        "source": {
            "candidate_release_sha256": candidate_sha,
            "deployed_release_sha256": deployed_sha,
            "candidate_commit_sha": candidate["source"]["commit_sha"],
            "deployed_commit_sha": deployed["source"]["commit_sha"],
            "recovery_cohort_id": cohort["cohort_id"],
            "active_cohort_id": cohort["cohort_id"],
            "active_cohort_sha256": cohort["contract_sha256"],
        },
        "schedule": {
            "timezone": "Europe/Paris",
            "service_date": local.date().isoformat(),
            "release_cutoff": "03:45",
            "window_opens": "04:00",
            "window_closes": "07:00",
        },
        "state": state,
        "writers": "open",
        "mutation_started": False,
        "production_reopened": False,
        "stages": stages,
        "pins": {
            "candidate": pins(candidate),
            "deployed": pins(deployed),
            "recovery": pins(deployed),
            "patch_sha256": None,
            "gitops": {
                name: {"expected_commit": None, "observed_commit": None}
                for name in ("candidate", "admitted", "recovery")
            },
        },
        "rollback": {"status": "not_required", "reason": None, "evidence_sha256": None},
        "incident": {"decision_required": False, "reason": None},
        "created_at": timestamp,
        "updated_at": timestamp,
        "contract_sha256": "0" * 64,
    }
    return with_checksum(value)


def initialize_state(path: Path, value: dict[str, Any]) -> bool:
    """Create state once, or prove an existing service-day run is compatible."""
    if not path.exists():
        atomic_write(path, value)
        return True
    current = validate(load_json(path))
    immutable = ("run_id", "mode", "source", "schedule")
    mismatched = [key for key in immutable if current[key] != value[key]]
    if mismatched:
        raise DeploymentRunError(
            "refusing to overwrite an incompatible existing run; mismatched: "
            + ", ".join(mismatched),
        )
    return False


def _validate_stage(value: object, index: int) -> dict[str, Any]:
    try:
        stage = exact_keys(
            value,
            {
                "name",
                "status",
                "attempts",
                "started_at",
                "completed_at",
                "evidence_sha256",
                "error",
            },
            f"run.stages[{index}]",
        )
    except ContractError as error:
        raise DeploymentRunError(str(error)) from error
    if stage["name"] != STAGES[index]:
        raise DeploymentRunError("run.stages must use the canonical order")
    if stage["status"] not in {"pending", "running", "succeeded", "failed", "skipped"}:
        raise DeploymentRunError(f"run.stages[{index}].status is invalid")
    if not isinstance(stage["attempts"], int) or stage["attempts"] < 0:
        raise DeploymentRunError(f"run.stages[{index}].attempts is invalid")
    for field in ("started_at", "completed_at"):
        if stage[field] is not None:
            try:
                validate_timestamp(stage[field], f"run.stages[{index}].{field}")
            except ContractError as error:
                raise DeploymentRunError(str(error)) from error
    if stage["evidence_sha256"] is not None:
        try:
            validate_sha256(
                stage["evidence_sha256"], f"run.stages[{index}].evidence_sha256",
            )
        except ContractError as error:
            raise DeploymentRunError(str(error)) from error
    if stage["error"] is not None and not isinstance(stage["error"], str):
        raise DeploymentRunError(f"run.stages[{index}].error must be null or a string")
    return stage


def validate(payload: object) -> dict[str, Any]:
    try:
        root = exact_keys(
            payload,
            {
                "schema",
                "run_id",
                "mode",
                "source",
                "schedule",
                "state",
                "writers",
                "mutation_started",
                "production_reopened",
                "stages",
                "pins",
                "rollback",
                "incident",
                "created_at",
                "updated_at",
                "contract_sha256",
            },
            "run",
        )
        validate_identifier(root["run_id"], "run.run_id")
        validate_timestamp(root["created_at"], "run.created_at")
        validate_timestamp(root["updated_at"], "run.updated_at")
        verify_checksum(root, "run")
    except ContractError as error:
        raise DeploymentRunError(str(error)) from error
    if root["schema"] != RUN_SCHEMA or root["mode"] not in {"release", "backup_only"}:
        raise DeploymentRunError("unsupported deployment run schema or mode")
    if root["state"] not in RUN_STATES:
        raise DeploymentRunError("run.state is invalid")
    try:
        source = exact_keys(
            root["source"],
            {
                "candidate_release_sha256",
                "deployed_release_sha256",
                "candidate_commit_sha",
                "deployed_commit_sha",
                "recovery_cohort_id",
                "active_cohort_id",
                "active_cohort_sha256",
            },
            "run.source",
        )
        validate_sha256(
            source["candidate_release_sha256"], "run.source.candidate_release_sha256",
        )
        validate_sha256(
            source["deployed_release_sha256"], "run.source.deployed_release_sha256",
        )
        validate_commit(
            source["candidate_commit_sha"], "run.source.candidate_commit_sha",
        )
        validate_commit(source["deployed_commit_sha"], "run.source.deployed_commit_sha")
        validate_identifier(
            source["recovery_cohort_id"], "run.source.recovery_cohort_id",
        )
        validate_identifier(source["active_cohort_id"], "run.source.active_cohort_id")
        validate_sha256(
            source["active_cohort_sha256"], "run.source.active_cohort_sha256",
        )
        schedule = exact_keys(
            root["schedule"],
            {
                "timezone",
                "service_date",
                "release_cutoff",
                "window_opens",
                "window_closes",
            },
            "run.schedule",
        )
    except ContractError as error:
        raise DeploymentRunError(str(error)) from error
    if schedule != {
        "timezone": "Europe/Paris",
        "service_date": schedule["service_date"],
        "release_cutoff": "03:45",
        "window_opens": "04:00",
        "window_closes": "07:00",
    }:
        raise DeploymentRunError(
            "run.schedule must use the governed Europe/Paris window",
        )
    if not isinstance(root["stages"], list) or len(root["stages"]) != len(STAGES):
        raise DeploymentRunError("run.stages must contain every canonical stage")
    stages = [
        _validate_stage(value, index) for index, value in enumerate(root["stages"])
    ]
    unresolved = False
    active_failures = 0
    for stage in stages:
        if stage["status"] in {"running", "failed"}:
            active_failures += 1
        if unresolved and stage["status"] == "succeeded":
            raise DeploymentRunError("run.stages cannot succeed out of order")
        if stage["status"] in {"pending", "running", "failed"}:
            unresolved = True
    if active_failures > 1:
        raise DeploymentRunError("run.stages has multiple active or failed stages")
    if root["mode"] == "backup_only":
        for stage in stages:
            if stage["name"] in RELEASE_ONLY_STAGES and stage["status"] != "skipped":
                raise DeploymentRunError(
                    "backup_only must skip every release-only stage",
                )
    try:
        pin_sets = exact_keys(
            root["pins"],
            {"candidate", "deployed", "recovery", "patch_sha256", "gitops"},
            "run.pins",
        )
        for set_name in ("candidate", "deployed", "recovery"):
            pin_set = exact_keys(
                pin_sets[set_name], set(ARTIFACT_ROLES), f"run.pins.{set_name}",
            )
            for role, reference in pin_set.items():
                validate_digest_reference(reference, f"run.pins.{set_name}.{role}")
    except ContractError as error:
        raise DeploymentRunError(str(error)) from error
    if pin_sets["recovery"] != pin_sets["deployed"]:
        raise DeploymentRunError(
            "recovery pins must exactly match the deployed release",
        )
    if pin_sets["patch_sha256"] is not None:
        validate_sha256(pin_sets["patch_sha256"], "run.pins.patch_sha256")
    gitops = exact_keys(
        pin_sets["gitops"], {"candidate", "admitted", "recovery"}, "run.pins.gitops",
    )
    for transition, identity in gitops.items():
        commits = exact_keys(
            identity,
            {"expected_commit", "observed_commit"},
            f"run.pins.gitops.{transition}",
        )
        for key, value in commits.items():
            if value is not None:
                validate_commit(value, f"run.pins.gitops.{transition}.{key}")
        if (
            commits["observed_commit"] is not None
            and commits["observed_commit"] != commits["expected_commit"]
        ):
            raise DeploymentRunError(f"run.pins.gitops.{transition} did not reconcile")
    by_name = {stage["name"]: stage for stage in stages}
    if (
        by_name["prepare_pins"]["status"] == "succeeded"
        and pin_sets["patch_sha256"] is None
    ):
        raise DeploymentRunError(
            "prepare_pins succeeded without a checksummed pin patch",
        )
    if (
        root["mutation_started"]
        and gitops["candidate"]["observed_commit"] is None
        and root["state"] not in {"rolled_back", "incident_required"}
    ):
        raise DeploymentRunError("mutation started without candidate GitOps sync proof")
    if root["production_reopened"] and gitops["admitted"]["observed_commit"] is None:
        raise DeploymentRunError("production reopened without admitted GitOps proof")
    if root["production_reopened"] and root["writers"] != "open":
        raise DeploymentRunError("reopened production must have open writers")
    if (
        root["production_reopened"]
        and not root["mutation_started"]
        and root["mode"] == "release"
    ):
        raise DeploymentRunError("release production cannot reopen before mutation")
    if root["state"] == "reopened" and not root["production_reopened"]:
        raise DeploymentRunError("reopened state requires production_reopened")
    if root["production_reopened"] and by_name["reopen"]["status"] != "succeeded":
        raise DeploymentRunError(
            "production_reopened requires a successful reopen stage",
        )
    try:
        rollback = exact_keys(
            root["rollback"], {"status", "reason", "evidence_sha256"}, "run.rollback",
        )
        incident = exact_keys(
            root["incident"], {"decision_required", "reason"}, "run.incident",
        )
    except ContractError as error:
        raise DeploymentRunError(str(error)) from error
    if rollback["status"] not in {
        "not_required",
        "pre_mutation_reopened",
        "running",
        "completed",
        "failed",
    }:
        raise DeploymentRunError("run.rollback.status is invalid")
    if rollback["status"] == "completed" and root["production_reopened"]:
        raise DeploymentRunError(
            "rollback recovery reopen is distinct from candidate production reopen",
        )
    if not isinstance(incident["decision_required"], bool):
        raise DeploymentRunError("run.incident.decision_required must be boolean")
    return root


def _alert(run: dict[str, Any], stage: str, outcome: str) -> None:
    url = os.environ.get("USL_ALERT_RELAY_URL")
    if not url:
        return
    secret = os.environ.get("USL_ALERT_RELAY_SECRET")
    secret_file = os.environ.get("USL_ALERT_RELAY_SECRET_FILE")
    if secret and secret_file:
        raise DeploymentRunError("configure only one alert relay secret source")
    if secret_file:
        secret = Path(secret_file).read_text(encoding="utf-8").strip()
    if not secret:
        raise DeploymentRunError("alert relay URL requires a secret")
    payload = json.dumps(
        {
            "schema": "usl-continuous-operations-alert/v1",
            "run_id": run["run_id"],
            "stage": stage,
            "outcome": outcome,
            "candidate_release_sha256": run["source"]["candidate_release_sha256"],
            "cohort_id": run["source"]["active_cohort_id"],
            "recovery_status": run["rollback"]["status"],
        },
    ).encode()
    request = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json", "X-Relay-Secret": secret},
    )
    with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
        if response.status < 200 or response.status >= 300:
            raise DeploymentRunError(f"alert relay returned HTTP {response.status}")


def _record_alert_failure(run: dict[str, Any], error: Exception) -> None:
    """Preserve the safety transition even when its secondary alert cannot deliver."""
    run["incident"]["decision_required"] = True
    detail = f"alert delivery failed: {error}"
    previous = run["incident"].get("reason")
    run["incident"]["reason"] = f"{previous}; {detail}" if previous else detail


def _run_hook(
    hook_dir: Path | None, name: str, run: dict[str, Any], *, dry_run: bool,
) -> str:
    if dry_run:
        return canonical_sha256(
            {"run_id": run["run_id"], "hook": name, "result": "dry-run"},
        )
    if hook_dir is None:
        raise DeploymentRunError("non-dry-run stages require --hook-dir")
    hook = hook_dir / name
    if not hook.is_file() or not os.access(hook, os.X_OK):
        raise DeploymentRunError(f"required executable hook is missing: {hook}")
    result = subprocess.run(
        [str(hook)],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "USL_DEPLOYMENT_RUN_JSON": json.dumps(run, sort_keys=True)},
    )
    if result.returncode:
        raise DeploymentRunError(
            (result.stderr or result.stdout or f"{name} failed").strip(),
        )
    result_dir = Path(os.environ.get("USL_STAGE_RESULT_DIR", "/evidence/stage-results"))
    stage_result = load_json(result_dir / f"{name}.json")
    exact_keys(
        stage_result,
        {"stage", "run_id", "status", "evidence_sha256"},
        f"{name} stage result",
    )
    if stage_result["stage"] != name or stage_result["run_id"] != run["run_id"]:
        raise DeploymentRunError(
            f"{name} stage result does not identify this invocation",
        )
    if stage_result["status"] != "succeeded":
        raise DeploymentRunError(f"{name} stage result is not successful")
    validate_sha256(
        stage_result["evidence_sha256"], f"{name} stage result.evidence_sha256",
    )
    return canonical_sha256(stage_result)


def _preflight_runtime(run: dict[str, Any], hook_dir: Path | None) -> None:
    if hook_dir is None or not hook_dir.is_dir():
        raise DeploymentRunError("validate requires the audited hook directory")
    required_stages = set(STAGES)
    if run["mode"] == "backup_only":
        required_stages -= RELEASE_ONLY_STAGES
    required = required_stages | RECOVERY_HOOKS
    missing = sorted(
        name
        for name in required
        if not (hook_dir / name).is_file() or not os.access(hook_dir / name, os.X_OK)
    )
    if missing:
        raise DeploymentRunError(f"audited operations hooks are incomplete: {missing}")
    for variable in (
        "USL_OPERATIONS_CONTRACT_ROOT",
        "USL_OPERATIONS_STATE_ROOT",
        "USL_OPERATIONS_EVIDENCE_ROOT",
        "USL_GITOPS_CHECKOUT",
    ):
        value = os.environ.get(variable)
        if not value or not Path(value).is_dir():
            raise DeploymentRunError(f"{variable} must identify a mounted directory")
    for variable in ("USL_EINVOICE_LIVE_ENABLED", "USL_EREPORTING_LIVE_ENABLED"):
        if os.environ.get(variable) != "0":
            raise DeploymentRunError(
                f"{variable} must be 0 for continuous-operations validation",
            )


def _rollback(
    run: dict[str, Any], hook_dir: Path | None, *, dry_run: bool, reason: str,
) -> None:
    if run["production_reopened"]:
        run["state"] = "incident_required"
        run["incident"] = {"decision_required": True, "reason": reason}
        return
    run["rollback"] = {"status": "running", "reason": reason, "evidence_sha256": None}
    evidence = []
    try:
        for hook in (
            "rollback_restore_cohort",
            "rollback_restore_pins",
            "rollback_verify",
            "rollback_reopen",
        ):
            evidence.append(_run_hook(hook_dir, hook, run, dry_run=dry_run))
        if dry_run:
            recovery_identity = {
                "expected_commit": "f" * 40,
                "observed_commit": "f" * 40,
            }
        else:
            result_path = Path(
                os.environ.get(
                    "USL_RECOVERY_GITOPS_RESULT", "/evidence/recovery-gitops.json",
                ),
            )
            recovery_identity = load_json(result_path)
            exact_keys(
                recovery_identity,
                {"expected_commit", "observed_commit"},
                "recovery GitOps result",
            )
            validate_commit(
                recovery_identity["expected_commit"], "recovery expected GitOps commit",
            )
            validate_commit(
                recovery_identity["observed_commit"], "recovery observed GitOps commit",
            )
            if (
                recovery_identity["expected_commit"]
                != recovery_identity["observed_commit"]
            ):
                raise DeploymentRunError("recovery GitOps commit was not reconciled")
        run["pins"]["gitops"]["recovery"] = recovery_identity
    except (DeploymentRunError, OSError) as error:
        run["rollback"] = {
            "status": "failed",
            "reason": str(error),
            "evidence_sha256": None,
        }
        run["state"] = "incident_required"
        run["incident"] = {
            "decision_required": True,
            "reason": "automatic rollback failed; writers remain paused",
        }
        run["writers"] = "paused"
        return
    run["rollback"] = {
        "status": "completed",
        "reason": reason,
        "evidence_sha256": canonical_sha256(evidence),
    }
    run["state"] = "rolled_back"
    run["writers"] = "open"


def _safe_pre_mutation_reopen(
    run: dict[str, Any], hook_dir: Path | None, *, dry_run: bool, reason: str,
) -> bool:
    """Verify unchanged production and reopen paused writers without a data restore."""
    if run["writers"] == "open":
        return True
    evidence = []
    try:
        for hook in ("pre_mutation_verify", "pre_mutation_reopen"):
            evidence.append(_run_hook(hook_dir, hook, run, dry_run=dry_run))
    except (DeploymentRunError, OSError) as error:
        run["state"] = "incident_required"
        run["writers"] = "paused"
        run["incident"] = {
            "decision_required": True,
            "reason": f"pre-mutation reopen failed: {error}",
        }
        return False
    run["writers"] = "open"
    run["rollback"] = {
        "status": "pre_mutation_reopened",
        "reason": reason,
        "evidence_sha256": canonical_sha256(evidence),
    }
    return True


def _apply_snapshot_result(
    run: dict[str, Any], result_path: Path | None, *, dry_run: bool,
) -> str:
    if result_path is None:
        if not dry_run:
            raise DeploymentRunError("snapshot requires --snapshot-result")
        return run["source"]["active_cohort_sha256"]
    result = load_json(result_path)
    exact_keys(
        result,
        {"cohort_path", "cohort_id", "cohort_contract_sha256"},
        "snapshot cohort result",
    )
    validate_identifier(result["cohort_id"], "snapshot cohort result.cohort_id")
    validate_sha256(
        result["cohort_contract_sha256"],
        "snapshot cohort result.cohort_contract_sha256",
    )
    if not isinstance(result["cohort_path"], str):
        raise DeploymentRunError("snapshot cohort_path must be a string")
    cohort_path = Path(result["cohort_path"])
    if not cohort_path.is_absolute():
        raise DeploymentRunError("snapshot cohort_path must be absolute")
    if not dry_run:
        evidence_root = Path(
            os.environ.get("USL_OPERATIONS_EVIDENCE_ROOT", "/evidence"),
        ).resolve()
        try:
            cohort_path.resolve().relative_to(evidence_root)
        except ValueError as error:
            raise DeploymentRunError(
                "snapshot cohort_path must stay under the evidence root",
            ) from error
    cohort = production_cohort.validate(load_json(cohort_path))
    if result["cohort_id"] != cohort["cohort_id"]:
        raise DeploymentRunError("snapshot result cohort_id does not match the cohort")
    if result["cohort_contract_sha256"] != cohort["contract_sha256"]:
        raise DeploymentRunError("snapshot result checksum does not match the cohort")
    if (
        cohort["release"]["release_contract_sha256"]
        != run["source"]["deployed_release_sha256"]
    ):
        raise DeploymentRunError(
            "snapshot cohort does not identify the deployed release",
        )
    run["source"]["active_cohort_id"] = cohort["cohort_id"]
    run["source"]["active_cohort_sha256"] = cohort["contract_sha256"]
    return canonical_sha256(result)


def _validate_upgrade_result(
    run: dict[str, Any], result_path: Path | None, *, dry_run: bool,
) -> str:
    expected = run["pins"]["gitops"]["candidate"]["expected_commit"]
    if result_path is None:
        if dry_run:
            return canonical_sha256({"deployed_commit": expected, "result": "dry-run"})
        raise DeploymentRunError("upgrade_production requires --upgrade-result")
    result = load_json(result_path)
    exact_keys(
        result,
        {"expected_deployed_commit", "observed_deployed_commit", "odoo_upgrade_sha256"},
        "upgrade result",
    )
    validate_commit(
        result["expected_deployed_commit"], "upgrade result.expected_deployed_commit",
    )
    validate_commit(
        result["observed_deployed_commit"], "upgrade result.observed_deployed_commit",
    )
    validate_sha256(result["odoo_upgrade_sha256"], "upgrade result.odoo_upgrade_sha256")
    if result["expected_deployed_commit"] != expected:
        raise DeploymentRunError(
            "upgrade result does not target the synced candidate GitOps commit",
        )
    if result["observed_deployed_commit"] != expected:
        raise DeploymentRunError(
            "DeployStack did not reach the synced candidate GitOps commit",
        )
    return canonical_sha256(result)


def _next_stage(run: dict[str, Any]) -> dict[str, Any] | None:
    for stage in run["stages"]:
        if stage["status"] == "pending":
            return stage
        if stage["status"] in {"running", "failed"}:
            return stage
    return None


def advance(
    run: dict[str, Any],
    stage_name: str,
    *,
    now: datetime,
    hook_dir: Path | None,
    dry_run: bool,
    fail_stage: str | None,
    pins_output: Path | None,
    expected_gitops_commit: str | None,
    observed_gitops_commit: str | None,
    gitops_result: Path | None,
    snapshot_result: Path | None,
    upgrade_result: Path | None,
) -> dict[str, Any]:
    validate(run)
    stage = next(item for item in run["stages"] if item["name"] == stage_name)
    if stage["status"] == "succeeded" or stage["status"] == "skipped":
        return run
    if run["state"] in {"deferred", "rolled_back", "incident_required", "recorded"}:
        raise DeploymentRunError(f"run is terminal: {run['state']}")
    if _next_stage(run) is not stage:
        raise DeploymentRunError(f"{stage_name} is out of order")
    local = now.astimezone(PARIS)
    before_mutation = not run["mutation_started"]
    if before_mutation and local.date().isoformat() != run["schedule"]["service_date"]:
        if not _safe_pre_mutation_reopen(
            run, hook_dir, dry_run=dry_run, reason="service date changed",
        ):
            return with_checksum(run)
        run["state"] = "deferred"
        return with_checksum(run)
    if before_mutation and local.time() >= time(7, 0):
        if not _safe_pre_mutation_reopen(
            run, hook_dir, dry_run=dry_run, reason="07:00 deadline before mutation",
        ):
            return with_checksum(run)
        run["state"] = "deferred"
        return with_checksum(run)
    if (
        run["mutation_started"]
        and not run["production_reopened"]
        and local.time() >= time(7, 0)
    ):
        _rollback(
            run, hook_dir, dry_run=dry_run, reason="07:00 deadline after mutation",
        )
        run["updated_at"] = now.isoformat().replace("+00:00", "Z")
        return with_checksum(run)
    if stage_name == "validate" and local.time() < time(4, 0):
        raise DeploymentRunError("the maintenance window has not opened")
    if stage_name == MUTATION_STAGE:
        if run["writers"] != "paused":
            raise DeploymentRunError("production upgrade requires paused writers")
        run["mutation_started"] = True
    timestamp = now.isoformat().replace("+00:00", "Z")
    stage.update(
        {
            "status": "running",
            "attempts": stage["attempts"] + 1,
            "started_at": timestamp,
            "error": None,
        },
    )
    if stage_name == "quiesce":
        run["writers"] = "paused"
    try:
        if fail_stage == stage_name:
            if not dry_run:
                raise DeploymentRunError(
                    "failure injection is allowed only in dry-run mode",
                )
            raise DeploymentRunError(f"injected failure at {stage_name}")
        if stage_name == "validate" and not dry_run:
            _preflight_runtime(run, hook_dir)
        evidence = _run_hook(hook_dir, stage_name, run, dry_run=dry_run)
        if stage_name == "snapshot":
            evidence = _apply_snapshot_result(run, snapshot_result, dry_run=dry_run)
        if stage_name == "prepare_pins":
            if pins_output is None:
                raise DeploymentRunError("prepare_pins requires --pins-output")
            patch = {
                key: run["pins"][key] for key in ("candidate", "deployed", "recovery")
            }
            pins_output.parent.mkdir(parents=True, exist_ok=True)
            pins_output.write_text(json.dumps(patch, indent=2) + "\n", encoding="utf-8")
            run["pins"]["patch_sha256"] = canonical_sha256(patch)
            evidence = run["pins"]["patch_sha256"]
        if stage_name in {"upgrade_production", "admit"}:
            if gitops_result is not None:
                result = load_json(gitops_result)
                exact_keys(
                    result,
                    {"expected_commit", "observed_commit"},
                    f"{stage_name} GitOps result",
                )
                expected_gitops_commit = result["expected_commit"]
                observed_gitops_commit = result["observed_commit"]
            if expected_gitops_commit is None or observed_gitops_commit is None:
                raise DeploymentRunError(
                    f"{stage_name} requires expected and observed GitOps commits",
                )
            validate_commit(expected_gitops_commit, "expected_gitops_commit")
            validate_commit(observed_gitops_commit, "observed_gitops_commit")
            if expected_gitops_commit != observed_gitops_commit:
                raise DeploymentRunError(
                    "Resource Sync/deploy did not reach the expected GitOps commit",
                )
            transition = (
                "candidate" if stage_name == "upgrade_production" else "admitted"
            )
            run["pins"]["gitops"][transition] = {
                "expected_commit": expected_gitops_commit,
                "observed_commit": observed_gitops_commit,
            }
        if stage_name == "upgrade_production":
            evidence = _validate_upgrade_result(run, upgrade_result, dry_run=dry_run)
        if stage_name == "reopen":
            run["writers"] = "open"
            if run["mode"] == "release":
                run["production_reopened"] = True
        stage.update(
            {
                "status": "succeeded",
                "completed_at": timestamp,
                "evidence_sha256": evidence,
            },
        )
        run["state"] = "recorded" if stage_name == "record" else stage_name
    except (ContractError, OSError, json.JSONDecodeError, ValueError) as error:
        stage.update(
            {"status": "failed", "completed_at": timestamp, "error": str(error)},
        )
        if run["mutation_started"]:
            _rollback(run, hook_dir, dry_run=dry_run, reason=str(error))
        else:
            if _safe_pre_mutation_reopen(
                run, hook_dir, dry_run=dry_run, reason=str(error),
            ):
                run["state"] = "failed_pre_mutation"
        try:
            _alert(run, stage_name, "failed")
        except (DeploymentRunError, OSError) as alert_error:
            _record_alert_failure(run, alert_error)
    run["updated_at"] = timestamp
    return with_checksum(run)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    validate_cmd = sub.add_parser("validate")
    validate_cmd.add_argument("path")
    init_cmd = sub.add_parser("init")
    init_cmd.add_argument(
        "--run-id",
        help="explicit identity; defaults to usl-continuous-YYYYMMDD in Europe/Paris",
    )
    init_cmd.add_argument(
        "--mode", choices=("release", "backup_only", "auto"), required=True,
    )
    init_cmd.add_argument("--deployed-release", required=True)
    init_cmd.add_argument("--candidate-release")
    init_cmd.add_argument("--candidate-prior-release")
    init_cmd.add_argument("--cohort", required=True)
    init_cmd.add_argument("--state", required=True)
    init_cmd.add_argument("--now")
    stage_cmd = sub.add_parser("stage")
    stage_cmd.add_argument("--state", required=True)
    stage_cmd.add_argument("--name", choices=STAGES, required=True)
    stage_cmd.add_argument("--now")
    stage_cmd.add_argument("--hook-dir")
    stage_cmd.add_argument("--dry-run", action="store_true")
    stage_cmd.add_argument("--fail-stage", choices=STAGES)
    stage_cmd.add_argument("--pins-output")
    stage_cmd.add_argument("--expected-gitops-commit")
    stage_cmd.add_argument("--observed-gitops-commit")
    stage_cmd.add_argument("--gitops-result")
    stage_cmd.add_argument("--snapshot-result")
    stage_cmd.add_argument("--upgrade-result")
    args = parser.parse_args()
    try:
        if args.command == "validate":
            validate(load_json(Path(args.path)))
            print(f"Valid {RUN_SCHEMA}: {args.path}")
            return 0
        if args.command == "init":
            value = initialize(
                run_id=args.run_id,
                mode=args.mode,
                deployed_release_path=Path(args.deployed_release),
                candidate_release_path=Path(args.candidate_release)
                if args.candidate_release
                else None,
                cohort_path=Path(args.cohort),
                now=parse_now(args.now),
                candidate_prior_release_path=(
                    Path(args.candidate_prior_release)
                    if args.candidate_prior_release
                    else None
                ),
            )
            created = initialize_state(Path(args.state), value)
            print(f"{args.state} ({'created' if created else 'unchanged'})")
            return 0
        state_path = Path(args.state)
        value = advance(
            load_json(state_path),
            args.name,
            now=parse_now(args.now),
            hook_dir=Path(args.hook_dir) if args.hook_dir else None,
            dry_run=args.dry_run,
            fail_stage=args.fail_stage,
            pins_output=Path(args.pins_output) if args.pins_output else None,
            expected_gitops_commit=args.expected_gitops_commit,
            observed_gitops_commit=args.observed_gitops_commit,
            gitops_result=Path(args.gitops_result) if args.gitops_result else None,
            snapshot_result=Path(args.snapshot_result)
            if args.snapshot_result
            else None,
            upgrade_result=Path(args.upgrade_result) if args.upgrade_result else None,
        )
        atomic_write(state_path, value)
        if value["state"] in {"failed_pre_mutation", "incident_required"}:
            return 2
        print(
            f"{args.name}: {next(item['status'] for item in value['stages'] if item['name'] == args.name)}",
        )
        return 0
    except (OSError, json.JSONDecodeError, ContractError, ValueError) as error:
        print(f"deployment run: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
