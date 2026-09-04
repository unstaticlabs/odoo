from __future__ import annotations

import argparse
import io
import json
import subprocess
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from operations.runtime import load_target
from operations.stack import (
    RECOVERY_PROOF_FAILURE_STAGES,
    RECOVERY_PROOF_OWNER,
    _cleanup_recovery_proof_resources,
    _backup_quiescence_receipt,
    _create_recovery_proof_resources,
    _digested_document,
    _recovery_proof_names,
    _recovery_proof_root,
    _recover_recovery_proof_backup,
    _validate_digested_document,
    _validate_recovery_proof_receipt,
    build_parser,
    recovery_proof_command,
)


ROOT = Path(__file__).resolve().parents[2]
TARGETS = ROOT / "operations/targets"
PROOF_ID = "daily-20260904-0400"


class ResourceRunner:
    def __init__(self) -> None:
        self.resources: dict[tuple[str, str], dict[str, str]] = {}
        self.commands: list[list[str]] = []
        self.files: dict[str, str] = {}

    @staticmethod
    def completed(command: list[str], output: str = "", returncode: int = 0):
        return subprocess.CompletedProcess(command, returncode, output, "")

    def run(self, command: list[str], *, check: bool = True, input_text: str | None = None):
        self.commands.append(command)
        if command[0] == "cat":
            value = self.files.get(command[1])
            return self.completed(command, value or "", 0 if value is not None else 1)
        if command[:2] == ["install", "-d"]:
            return self.completed(command)
        if command[:2] == ["test", "-e"]:
            return self.completed(command, returncode=1)
        if command[:2] == ["docker", "inspect"]:
            resource_type = "container"
            name = command[2]
            labels = self.resources.get((resource_type, name))
            return self.completed(
                command,
                json.dumps(labels) if labels is not None else "",
                0 if labels is not None else 1,
            )
        if command[:3] in (["docker", "volume", "inspect"], ["docker", "network", "inspect"]):
            resource_type = command[1]
            name = command[3]
            labels = self.resources.get((resource_type, name))
            return self.completed(
                command,
                json.dumps(labels) if labels is not None else "",
                0 if labels is not None else 1,
            )
        if command[:3] in (["docker", "volume", "create"], ["docker", "network", "create"]):
            resource_type = command[1]
            name = command[-1]
            labels = {
                item.split("=", 1)[0]: item.split("=", 1)[1]
                for index, item in enumerate(command)
                if index > 0 and command[index - 1] == "--label"
            }
            self.resources[(resource_type, name)] = labels
            return self.completed(command, name + "\n")
        if command[:3] in (["docker", "volume", "rm"], ["docker", "network", "rm"]):
            self.resources.pop((command[1], command[3]), None)
            return self.completed(command)
        if command[:3] == ["docker", "rm", "--force"]:
            self.resources.pop(("container", command[3]), None)
            return self.completed(command)
        if command[:3] == ["rm", "-rf", "--"]:
            return self.completed(command)
        if "compose" in command and "up" in command:
            return self.completed(command)
        raise AssertionError(command)


def completion_receipt(proof_id: str = PROOF_ID) -> dict:
    digest = "a" * 64
    return _digested_document({
        "schema": "usl-disposable-recovery-proof/v1",
        "proof_id": proof_id,
        "source": "production",
        "release": {"identity": digest, "manifest_sha256": digest},
        "backup": {
            "run_id": f"proof-{proof_id}",
            "receipt_sha256": digest,
            "durable_snapshot_id": digest,
            "cache_snapshot_id": digest,
        },
        "materialization": {},
        "health": {"status": "passed"},
        "smoke": {"status": "passed"},
        "reusable_cache": {},
        "ownership": {"label": RECOVERY_PROOF_OWNER},
        "cleanup": {"status": "clean"},
        "isolation": {
            "active_runtime_sha256": digest,
            "active_runtime_unchanged": True,
            "gateway_attached": False,
            "application_workers_started": False,
            "persistent_staging_touched": False,
            "production_secrets_modified": False,
            "runtime_ledger_used_for_restore": False,
        },
        "started_at": "2026-09-04T04:00:00Z",
        "completed_at": "2026-09-04T04:10:00Z",
        "status": "passed",
    })


class RecoveryProofContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.target = load_target("production", TARGETS)

    def test_public_parser_requires_explicit_identity_and_evidence_perimeter(self) -> None:
        arguments = build_parser().parse_args([
            "recovery-proof", "run", "--target", "production",
            "--proof-id", PROOF_ID,
            "--evidence-directory", "/var/lib/usl-recovery-proofs",
            "--failure-after", "materialized", "--json",
        ])
        self.assertIs(arguments.handler, recovery_proof_command)
        self.assertEqual(arguments.failure_after, "materialized")
        self.assertEqual(
            set(RECOVERY_PROOF_FAILURE_STAGES),
            {"backup-qualified", "resources-created", "materialized", "validated"},
        )

    def test_evidence_perimeter_cannot_overlap_runtime_storage_or_secrets(self) -> None:
        self.assertEqual(
            _recovery_proof_root(
                self.target, Path("/var/lib/usl-recovery-proofs"), PROOF_ID,
            ),
            f"/var/lib/usl-recovery-proofs/{PROOF_ID}",
        )
        for path in (
            "/var/lib/usl-odoo/runtime/production/proofs",
            "/srv/storage/proofs",
            "/opt/usl-odoo/secrets/proofs",
            "relative/proofs",
        ):
            with self.subTest(path=path), self.assertRaises(RuntimeError):
                _recovery_proof_root(self.target, Path(path), PROOF_ID)

    def test_resources_have_unique_proof_ownership_and_cleanup_is_exact(self) -> None:
        runner = ResourceRunner()
        names = _create_recovery_proof_resources(self.target, runner, PROOF_ID)
        expected_names = _recovery_proof_names(self.target, PROOF_ID)
        self.assertEqual(names, expected_names)
        for (resource_type, _name), labels in runner.resources.items():
            self.assertIn(resource_type, {"volume", "network"})
            self.assertEqual(labels["com.unstaticlabs.recovery-proof.owner"], RECOVERY_PROOF_OWNER)
            self.assertEqual(labels["com.unstaticlabs.recovery-proof.id"], PROOF_ID)
            self.assertEqual(labels["com.unstaticlabs.recovery-proof.source"], "production")
        cleanup = _cleanup_recovery_proof_resources(
            self.target,
            runner,
            PROOF_ID,
            f"/var/lib/usl-recovery-proofs/{PROOF_ID}",
        )
        self.assertEqual(cleanup["status"], "clean")
        self.assertFalse(runner.resources)
        self.assertFalse(any("compose" in command for command in runner.commands))

    def test_retry_cleanup_refuses_a_foreign_name_collision(self) -> None:
        runner = ResourceRunner()
        names = _recovery_proof_names(self.target, PROOF_ID)
        runner.resources[("volume", names["volumes"]["odoo_postgres"])] = {
            "com.unstaticlabs.recovery-proof.owner": "somebody-else",
        }
        with self.assertRaisesRegex(RuntimeError, "refusing foreign volume"):
            _cleanup_recovery_proof_resources(
                self.target,
                runner,
                PROOF_ID,
                f"/var/lib/usl-recovery-proofs/{PROOF_ID}",
            )
        self.assertIn(("volume", names["volumes"]["odoo_postgres"]), runner.resources)

    def test_receipts_are_digest_bound_and_fail_closed_on_tampering(self) -> None:
        receipt = completion_receipt()
        self.assertEqual(_validate_recovery_proof_receipt(receipt, PROOF_ID), receipt)
        receipt["isolation"]["persistent_staging_touched"] = True
        with self.assertRaisesRegex(RuntimeError, "digest differs"):
            _validate_recovery_proof_receipt(receipt, PROOF_ID)
        receipt = completion_receipt()
        receipt["isolation"]["persistent_staging_touched"] = True
        receipt["sha256"] = _digested_document(
            {key: value for key, value in receipt.items() if key != "sha256"}
        )["sha256"]
        with self.assertRaisesRegex(RuntimeError, "completion receipt is invalid"):
            _validate_recovery_proof_receipt(receipt, PROOF_ID)

    def test_completed_retry_returns_receipt_without_inspecting_or_mutating_runtime(self) -> None:
        runner = ResourceRunner()
        proof_root = f"/var/lib/usl-recovery-proofs/{PROOF_ID}"
        runner.files[f"{proof_root}/receipt.json"] = json.dumps(completion_receipt())
        wrapped = type("WrappedTarget", (), {
            "name": "production",
            "value": self.target.value,
            "runner": lambda _self: runner,
        })()
        output = io.StringIO()
        arguments = argparse.Namespace(
            target="production",
            targets=TARGETS,
            proof_id=PROOF_ID,
            evidence_directory=Path("/var/lib/usl-recovery-proofs"),
            release=None,
            failure_after=None,
            json=True,
        )
        with (
            patch("operations.stack.load_target", return_value=wrapped),
            patch("operations.stack.inspect_runtime", side_effect=AssertionError("runtime touched")),
            redirect_stdout(output),
        ):
            self.assertEqual(recovery_proof_command(arguments), 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "passed")
        self.assertEqual(
            [command for command in runner.commands if command[0] not in {"cat", "install"}],
            [],
        )

    def test_interrupted_backup_retry_resumes_exact_writers_and_capture(self) -> None:
        runner = ResourceRunner()
        run_id = f"proof-{PROOF_ID}"
        root = f"{self.target.value['state_directory']}/backup-runs/{run_id}"
        runner.files[f"{root}/capture.json"] = json.dumps({
            "run_id": run_id,
            "target": "production",
        })
        services = [
            self.target.value["services"][name]
            for name in ("odoo", "paperless", "mcp", "sign", "sign_ca")
        ]
        quiescence = _backup_quiescence_receipt(
            target="production",
            run_id=run_id,
            baseline="a" * 64,
            services=services,
            status="quiesced",
            prepared_at="2026-09-04T04:00:00Z",
            stopped_at="2026-09-04T04:00:01Z",
        )
        runner.files[f"{root}/quiesced.json"] = json.dumps(quiescence)
        with (
            patch("operations.stack.validate_cohort_manifest"),
            patch("operations.stack._recover_interrupted_backup_lock") as recover_lock,
            patch("operations.stack.compose_identity", return_value={
                "project": "production",
                "working_directory": "/release",
                "environment_file": "/runtime/production.env",
                "compose_files": ["compose.yaml"],
                "profiles": [],
            }),
            patch("operations.stack.inspect_runtime", return_value={}),
            patch("operations.stack._runtime_cas_sha256", return_value="a" * 64),
        ):
            _recover_recovery_proof_backup(
                self.target, runner, run_id, "a" * 64,
            )
        recover_lock.assert_called_once_with(
            self.target,
            runner,
            run_id=run_id,
            quiescence=quiescence,
        )
        compose = next(command for command in runner.commands if "compose" in command)
        self.assertEqual(compose[-len(services):], services)
        self.assertIn("--no-recreate", compose)

    def test_state_evidence_rejects_recomputed_unknown_fields(self) -> None:
        state = _digested_document({
            "schema": "usl-disposable-recovery-proof-state/v1",
            "proof_id": PROOF_ID,
            "source": "production",
            "phase": "backup-qualified",
            "release_identity": "a" * 64,
            "release_manifest_sha256": "b" * 64,
            "runtime_sha256": "c" * 64,
            "backup": None,
            "updated_at": "2026-09-04T04:00:00Z",
        })
        state["unexpected"] = True
        state = _digested_document({key: value for key, value in state.items() if key != "sha256"})
        with self.assertRaisesRegex(RuntimeError, "fields differ"):
            _validate_digested_document(
                state,
                schema="usl-disposable-recovery-proof-state/v1",
                proof_id=PROOF_ID,
            )


if __name__ == "__main__":
    unittest.main()
