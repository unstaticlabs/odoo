from __future__ import annotations

import argparse
import io
import json
import subprocess
import unittest
from contextlib import redirect_stdout
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from operations.runtime import load_target
from operations.stack import (
    RECOVERY_PROOF_MAX_SECONDS,
    RECOVERY_PROOF_RUNTIME_ROLES,
    RECOVERY_PROOF_FAILURE_STAGES,
    RECOVERY_PROOF_OWNER,
    _cleanup_recovery_proof_resources,
    _backup_quiescence_receipt,
    _create_recovery_proof_resources,
    _digested_document,
    _materialize_command,
    _recovery_proof_names,
    _recovery_proof_environment,
    _recovery_proof_root,
    _run_recovery_proof_container,
    _start_recovery_proof_database,
    _recover_recovery_proof_backup,
    _validate_digested_document,
    _validate_recovery_proof_receipt,
    _write_remote,
    _write_recovery_proof_failure,
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
        if command[0] in {"mkdir", "rmdir"}:
            return self.completed(command)
        if command[:2] == ["python3", "-c"]:
            self.files[command[3]] = input_text or ""
            return self.completed(command)
        if command[:2] == ["test", "-e"]:
            return self.completed(command, returncode=1)
        if command[:2] == ["test", "-L"]:
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
        if command[:3] == ["docker", "ps", "--all"]:
            return self.completed(command)
        if command[:3] in (["docker", "volume", "ls"], ["docker", "network", "ls"]):
            return self.completed(command)
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
        if command[:3] == ["docker", "run", "--detach"]:
            name = command[command.index("--name") + 1]
            labels = {
                item.split("=", 1)[0]: item.split("=", 1)[1]
                for index, item in enumerate(command)
                if index > 0 and command[index - 1] == "--label"
            }
            self.resources[("container", name)] = labels
            return self.completed(command, name + "\n")
        if command[:2] == ["docker", "exec"]:
            return self.completed(command)
        if command[:3] in (["docker", "volume", "rm"], ["docker", "network", "rm"]):
            self.resources.pop((command[1], command[3]), None)
            return self.completed(command)
        if command[:3] == ["docker", "rm", "--force"]:
            self.resources.pop(("container", command[3]), None)
            return self.completed(command)
        if command[:3] == ["rm", "-rf", "--"]:
            return self.completed(command)
        if command[:3] == ["rm", "-f", "--"]:
            self.files.pop(command[3], None)
            return self.completed(command)
        if "compose" in command and "up" in command:
            return self.completed(command)
        raise AssertionError(command)

    @contextmanager
    def advisory_lock(self, path: str):
        self.commands.append(["flock", "--nonblock", path])
        yield


def completion_receipt(proof_id: str = PROOF_ID) -> dict:
    digest = "a" * 64
    return _digested_document({
        "schema": "usl-disposable-recovery-proof/v2",
        "proof_id": proof_id,
        "source": "production",
        "release": {"identity": digest, "manifest_sha256": digest},
        "backup": {
            "run_id": f"proof-{proof_id}",
            "receipt_sha256": digest,
            "durable_snapshot_id": digest,
            "cache_snapshot_id": digest,
        },
        "materialization": {
            "cohort_schema": "usl-recovery-cohort/v2",
            "capacity": {
                "schema": "usl-recovery-proof-capacity/v1", "source": "/dev/disk",
                "available_bytes": 3, "candidate_bytes": 1, "reserve_bytes": 1,
                "required_bytes": 2, "status": "passed",
            },
            "controls_sha256": digest,
            "sign_secrets_restored": True, "mcp_secrets_restored": True,
            "renderer_secrets_restored": True,
            "paperless_personal_ai_keys_restored": True, "status": "materialized",
        },
        "runtime": {
            "network": "internal",
            "services": {
                role: {"container_name_sha256": digest, "status": "ready"}
                for role in RECOVERY_PROOF_RUNTIME_ROLES
            },
            "environment": {
                "environment_sha256": {
                    role: digest
                    for role in (
                        "database", "odoo", "paperless", "mcp", "dss",
                        "better-auth", "credential-encryption-key", "personal-ai",
                    )
                },
                "status": "passed",
            },
            "quarantine": {
                "candidate_fingerprint": digest,
                "cron_count": 0,
                "database_neutralized": True,
                "fetchmail_count": 0,
                "status": "passed",
            },
            "status": "passed",
        },
        "health": {
            "checks": {
                **{f"{role}_running": True for role in RECOVERY_PROOF_RUNTIME_ROLES},
                "odoo_http": True, "paperless_http": True, "gotenberg_http": True,
                "tika_http": True, "paperless_redis": True, "mcp_ready": True,
                "mcp_unauthenticated_boundary": True, "mcp_vault_decrypt": True, "step_ca": True,
                "renderer_mtls_health": True, "sign_cryptographic_transaction": True,
            },
            "databases": {"odoo": True, "paperless": True},
            "checked_at": "2026-09-04T04:09:00Z", "status": "passed",
        },
        "smoke": {
            "runtime": {
                "http": {"odoo": "passed", "paperless": "passed", "gotenberg": "passed", "tika": "passed"},
                "oauth": {
                    "readiness": "passed", "unauthenticated_boundary": "passed", "schema_version": 1,
                    "active_enrollments": 1, "refreshed": 1, "cached": 0,
                    "unavailable": 0, "vault_readability": "decrypted",
                },
                "signing": {"step_ca": "passed", "cryptographic_transaction": "passed", "renderer_mtls": "passed"},
                "status": "passed",
            },
            "controls": {"status": "passed"}, "status": "passed",
        },
        "durable_state": {
            "checked_at": "2026-09-04T04:09:30Z",
            "mcp_oauth": {
                "schema_version": 1, "vault_identity_sha256": digest,
                "recovered_key_material_sha256": digest,
                "vault_key_binding_sha256": digest,
                "migration": "passed", "readability": "passed", "status": "passed",
            },
            "odoo": {"attachment_records": 1, "status": "passed"},
            "paperless": {"document_records": 1, "status": "passed"},
            "samples": {
                role: {"file_count": 1, "sample_content_sha256": digest, "status": "passed"}
                for role in (
                    "odoo_filestore", "paperless_media", "paperless_archive",
                    "paperless_thumbnails", "paperless_tantivy", "paperless_vectors",
                )
            },
            "status": "passed",
        },
        "reusable_cache": {
            **{
                role: {
                    "capture_identity_sha256": digest, "file_count": 1,
                    "sample_content_sha256": digest, "status": "passed",
                }
                for role in (
                    "paperless_archive", "paperless_thumbnails",
                    "paperless_tantivy", "paperless_vectors",
                )
            },
            "status": "reused",
        },
        "ownership": {"label": RECOVERY_PROOF_OWNER, "resource_names_sha256": digest},
        "cleanup": {
            "schema": "usl-recovery-proof-cleanup/v1", "containers": [],
            "volumes": [], "networks": [], "workspaces": [], "status": "clean",
        },
        "isolation": {
            "active_runtime_sha256": digest,
            "active_runtime_unchanged": True,
            "gateway_attached": False,
            "host_ports_published": False,
            "external_networks_attached": False,
            "side_effects_neutralized": True,
            "persistent_staging_touched": False,
            "production_secrets_modified": False,
            "production_secrets_sha256": digest,
            "runtime_ledger_used_for_restore": False,
            "perimeter_sha256": digest,
        },
        "started_at": "2026-09-04T04:00:00Z",
        "completed_at": "2026-09-04T04:10:00Z",
        "duration_seconds": 600.0,
        "max_duration_seconds": RECOVERY_PROOF_MAX_SECONDS,
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
            {
                "backup-qualified", "resources-created", "materialized",
                "runtime-started", "validated", "cleanup", "cas-verified", "final-write",
            },
        )

    def test_evidence_perimeter_cannot_overlap_runtime_storage_or_secrets(self) -> None:
        self.assertEqual(
            _recovery_proof_root(
                self.target, Path("/var/lib/usl-recovery-proofs"), PROOF_ID,
            ),
            f"/var/lib/usl-recovery-proofs/{PROOF_ID}",
        )
        self.assertEqual(
            _recovery_proof_root(self.target, Path("/srv/storage/proofs"), PROOF_ID),
            f"/srv/storage/proofs/{PROOF_ID}",
        )
        for path in (
            "/var/lib/usl-odoo/runtime/production/proofs",
            "/srv/storage",
            "/srv",
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
        network_create = next(
            command for command in runner.commands
            if command[:3] == ["docker", "network", "create"]
        )
        self.assertIn("--internal", network_create)
        self.assertEqual(
            set(names["containers"]),
            {"odoo_db", "paperless_db", "materializer", *RECOVERY_PROOF_RUNTIME_ROLES},
        )

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

    def test_runtime_container_has_only_internal_network_and_no_host_publication(self) -> None:
        runner = ResourceRunner()
        names = _recovery_proof_names(self.target, PROOF_ID)
        _run_recovery_proof_container(
            runner, PROOF_ID, names, "odoo", "odoo@sha256:" + "a" * 64,
            alias="odoo", env_file="/proof/odoo.env",
            volumes=[f"{names['volumes']['odoo_filestore']}:/var/lib/odoo"],
        )
        command = runner.commands[-1]
        self.assertNotIn("--publish", command)
        self.assertNotIn("-p", command)
        self.assertEqual(command[command.index("--network") + 1], names["network"])
        self.assertNotIn(self.target.value["secrets"]["env_file"], command)

    def test_database_uses_exact_database_role_name_for_start_and_readiness(self) -> None:
        runner = ResourceRunner()
        names = _recovery_proof_names(self.target, PROOF_ID)
        runner.resources[("container", names["containers"]["odoo_db"])] = {}
        images = {self.target.value["services"]["odoo_db"]: "postgres@sha256:" + "a" * 64}
        _start_recovery_proof_database(
            self.target, runner, PROOF_ID, names, images, "odoo", "/proof/database.env",
        )
        start = next(command for command in runner.commands if command[:3] == ["docker", "run", "--detach"])
        self.assertEqual(start[start.index("--name") + 1], names["containers"]["odoo_db"])
        ready = next(command for command in runner.commands if command[:2] == ["docker", "exec"])
        self.assertEqual(ready[2], names["containers"]["odoo_db"])

    def test_materializer_has_repository_egress_and_internal_database_dns(self) -> None:
        names = _recovery_proof_names(self.target, PROOF_ID)
        command = _materialize_command(
            self.target, self.target, "backup@sha256:" + "a" * 64, "b" * 64,
            "gproof", [names["materialization_network"], names["network"]],
            names["volumes"], "/proof/source.env", "/proof/database.env",
            (PROOF_ID, names["containers"]["materializer"]),
        )
        networks = [command[index + 1] for index, item in enumerate(command) if item == "--network"]
        self.assertEqual(networks, [names["materialization_network"], names["network"]])
        self.assertEqual(command[command.index("--name") + 1], names["containers"]["materializer"])
        self.assertIn(f"ODOO_DB_HOST={self.target.value['databases']['odoo']['service']}", command)
        self.assertIn(
            f"PAPERLESS_DB_HOST={self.target.value['databases']['paperless']['service']}", command,
        )
        self.assertNotIn(self.target.value["secrets"]["env_file"], command)

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

    def test_receipt_rejects_recomputed_nested_status_and_duration_tampering(self) -> None:
        receipt = completion_receipt()
        receipt["health"]["status"] = "ready"
        receipt = _digested_document({key: value for key, value in receipt.items() if key != "sha256"})
        with self.assertRaisesRegex(RuntimeError, "completion receipt is invalid"):
            _validate_recovery_proof_receipt(receipt, PROOF_ID)

        receipt = completion_receipt()
        receipt["duration_seconds"] = RECOVERY_PROOF_MAX_SECONDS
        receipt = _digested_document({key: value for key, value in receipt.items() if key != "sha256"})
        with self.assertRaisesRegex(RuntimeError, "completion receipt is invalid"):
            _validate_recovery_proof_receipt(receipt, PROOF_ID)

        receipt = completion_receipt()
        receipt["completed_at"] = "2026-09-04T03:59:59Z"
        receipt = _digested_document({key: value for key, value in receipt.items() if key != "sha256"})
        with self.assertRaisesRegex(RuntimeError, "completion duration is invalid"):
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
        self.assertFalse(any(command[0] == "docker" for command in runner.commands))

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
            "schema": "usl-disposable-recovery-proof-state/v2",
            "proof_id": PROOF_ID,
            "source": "production",
            "phase": "backup-qualified",
            "release_identity": "a" * 64,
            "release_manifest_sha256": "b" * 64,
            "runtime_sha256": "c" * 64,
            "backup": None,
            "started_at": "2026-09-04T04:00:00Z",
            "deadline_at": "2026-09-04T04:30:00Z",
            "updated_at": "2026-09-04T04:00:00Z",
            "duration_seconds": 0.0,
        })
        state["unexpected"] = True
        state = _digested_document({key: value for key, value in state.items() if key != "sha256"})
        with self.assertRaisesRegex(RuntimeError, "fields differ"):
            _validate_digested_document(
                state,
                schema="usl-disposable-recovery-proof-state/v2",
                proof_id=PROOF_ID,
            )

    def test_failure_evidence_is_atomic_digest_bound_and_records_cleanup_failure(self) -> None:
        runner = ResourceRunner()
        root = f"/var/lib/usl-recovery-proofs/{PROOF_ID}"
        cleanup = {
            "schema": "usl-recovery-proof-cleanup/v1", "status": "failed",
            "error_sha256": "b" * 64,
        }
        evidence = _write_recovery_proof_failure(
            self.target, runner, root, PROOF_ID, "cleanup",
            RuntimeError("injected cleanup failure"), cleanup, "a" * 64,
            "2026-09-04T04:00:00Z", 0.0,
        )
        self.assertEqual(evidence["stage"], "cleanup")
        self.assertEqual(evidence["cleanup"]["status"], "failed")
        self.assertEqual(
            json.loads(runner.files[f"{root}/failure.json"]),
            evidence,
        )

    def test_remote_atomic_write_never_places_secret_content_in_argv(self) -> None:
        runner = ResourceRunner()
        secret = "DB_PASSWORD=argv-leak-sentinel"
        _write_remote(self.target, runner, "/proof/secret.env", secret)
        command = runner.commands[-1]
        joined = " ".join(command)
        self.assertNotIn(secret, joined)
        self.assertNotIn("YXJndi1sZWFrLXNlbnRpbmVs", joined)
        self.assertEqual(runner.files["/proof/secret.env"], secret)

    def test_paperless_database_password_is_bound_from_rendered_compose_key(self) -> None:
        runner = ResourceRunner()
        secret = "paperless-db-sentinel"
        rendered = {"services": {
            "odoo": {"environment": {"ODOO_DB_PASSWORD": "odoo-secret"}},
            "paperless-webserver": {"environment": {
                "PAPERLESS_DBPASS": secret, "PAPERLESS_SECRET_KEY": "paperless-secret",
            }},
            "odoo-mcp": {"environment": {}},
            "usl-sign-dss": {"environment": {}},
        }}
        paths, receipt = _recovery_proof_environment(
            self.target, runner, f"/var/lib/usl-recovery-proofs/{PROOF_ID}",
            rendered, "a" * 64,
            "b" * 64,
        )
        self.assertIn(f"PAPERLESS_DBPASS={secret}\n", runner.files[paths["database"]])
        self.assertIn(f"PAPERLESS_DB_PASSWORD={secret}\n", runner.files[paths["database"]])
        self.assertIn(f"PAPERLESS_DBPASS={secret}\n", runner.files[paths["paperless"]])
        self.assertNotIn(secret, json.dumps(receipt))
        self.assertNotIn(secret, " ".join(part for command in runner.commands for part in command))

    def test_failure_receipt_hashes_error_without_persisting_secret(self) -> None:
        runner = ResourceRunner()
        root = f"/var/lib/usl-recovery-proofs/{PROOF_ID}"
        secret = "argv-leak-sentinel"
        evidence = _write_recovery_proof_failure(
            self.target, runner, root, PROOF_ID, "cleanup",
            RuntimeError(secret), {"schema": "usl-recovery-proof-cleanup/v1", "status": "clean"},
            "a" * 64, "2026-09-04T04:00:00Z", 0.0,
        )
        self.assertNotIn(secret, json.dumps(evidence))
        self.assertNotIn(secret, " ".join(part for command in runner.commands for part in command))


if __name__ == "__main__":
    unittest.main()
