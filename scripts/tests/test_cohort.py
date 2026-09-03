from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from operations import cohort
from operations.runtime import load_target
from operations.stack import (
    BACKUP_WRITER_SERVICE_ROLES,
    VOLUME_LOGICAL_NAMES,
    _cohort_command,
    _apply_generation_cron_policy,
    _abort_to_previous_generation,
    _generation_overlay,
    _ensure_image,
    _materialize_command,
    _prepare_generation_volume_ownership,
    _previous_generation_identity,
    _release_images,
    _remove_materialization_workspace,
    _require_restore_capacity,
    _measure_candidate_bytes,
    _notify_release,
    _rollback_after_failure,
    _restore_unlocked,
    _validate_materialized_release,
    _validate_runtime_release_images,
    generation_volume_names,
    runtime_lock,
    with_writers_paused,
)


ROOT = Path(__file__).resolve().parents[2]
TARGETS = ROOT / "operations/targets"


def manifest(durable: dict, cache: dict) -> dict:
    return {
        "schema": cohort.SCHEMA,
        "run_id": "20260901t120000z-a1b2c3d4",
        "created_at": "2026-09-01T12:00:00Z",
        "target": "production",
        "release": {
            "commit": "a" * 40,
            "manifest_sha256": "b" * 64,
            "path": "durable/release.json",
        },
        "ollama": {"model": "bge-m3:latest", "manifest_sha256": "c" * 64, "dimension": 1024},
        "databases": {
            "odoo": {"name": "odoo_production", "bytes": 1, "sha256": "d" * 64},
            "paperless": {"name": "paperless", "bytes": 1, "sha256": "e" * 64},
        },
        "controls": {
            "odoo": {"ledger_delta": 0},
            "paperless": {"documents": 1},
        },
        "durable": durable,
        "cache": cache,
        "resources": {
            "odoo_filestore": {
                "class": "durable",
                "path": "durable/odoo-filestore",
                "identity": durable,
            },
            "sign_secrets": {
                "class": "durable",
                "path": "durable/sign-secrets",
                "identity": durable,
            },
        },
        "cache_snapshot_id": None,
    }


class CohortContractTests(unittest.TestCase):
    def test_release_notification_is_bound_to_active_release_and_persistent_channel(self) -> None:
        release_id = "a" * 64
        target = mock.Mock(value={
            "environment": "production",
            "compose": {"default_network": "production-network"},
            "databases": {"odoo": {"service": "db", "name": "odoo", "user": "odoo"}},
            "secrets": {"env_file": "/secrets.env"},
        })
        runner = mock.Mock()
        runner.run.return_value = subprocess.CompletedProcess(
            [], 0,
            'USL_RELEASE_NOTIFICATION_RESULT={"channel": "mail.channel_all_employees", "message_id": 42, "release": "' + release_id + '", "status": "posted"}\n',
            "",
        )
        runtime = {
            "active_state": None,
            "volumes": {"odoo_filestore": {"name": "odoo-data"}},
        }
        release = {
            "identity": release_id,
            "build": {"workflow_url": "https://github.com/unstaticlabs/odoo/actions/runs/1"},
            "components": {"distribution": {"digest_reference": "odoo@sha256:" + "b" * 64}},
            "release_notes": {
                "schema": "usl-release-notes/v1",
                "title": "Safer releases",
                "summary": "The release is active.",
                "changes": ["Improved recovery."],
                "action_required": None,
            },
        }
        with mock.patch("operations.stack.inspect_runtime", return_value=runtime), mock.patch(
            "operations.stack._release", return_value=(release, "c" * 64, "{}")
        ):
            result = _notify_release(target, runner, release_id)
        self.assertEqual(result["message_id"], 42)
        program = runner.run.call_args.kwargs["input_text"]
        self.assertIn("mail.channel_all_employees", program)
        self.assertIn("base.partner_root", program)
        self.assertIn("message_post", program)
        self.assertNotIn("_bus_send", program)

    def test_release_notification_rejects_untrusted_identity(self) -> None:
        target = mock.Mock(value={"environment": "production"})
        with self.assertRaisesRegex(RuntimeError, "identity"):
            _notify_release(target, mock.Mock(), "../release")

    def test_retention_keeps_latest_daily_weekly_monthly_and_yearly_points(self) -> None:
        now = datetime(2026, 9, 3, 12, tzinfo=UTC)
        snapshots = [
            {
                "id": f"{index:064x}",
                "time": (now - timedelta(days=index)).isoformat(),
                "tags": ["usl-cohort", "durable", "recovery-eligible", "target-production"],
            }
            for index in range(500)
        ]
        retained = cohort.select_retained_snapshots(snapshots)
        self.assertIn(f"{0:064x}", retained)
        self.assertIn(f"{13:064x}", retained)
        self.assertNotIn(f"{499:064x}", retained)
        self.assertGreaterEqual(len(retained), 14)

    def test_retention_keeps_cache_for_every_retained_durable_run(self) -> None:
        now = datetime(2026, 9, 3, 12, tzinfo=UTC)
        release = "release-" + "a" * 64

        def item(index, classification, age):
            return {
                "id": f"{index:064x}",
                "time": (now - timedelta(days=age)).isoformat(),
                "tags": [
                    "usl-cohort", classification, "target-production",
                    "recovery-eligible" if classification == "durable" else release,
                    f"run-2026-run-{index:02d}",
                ],
            }

        durable = [item(1, "durable", 0), item(2, "durable", 1000)]
        cache = [item(11, "cache", 0), item(12, "cache", 1000)]
        cache[0]["tags"][-1] = durable[0]["tags"][-1]
        cache[1]["tags"][-1] = durable[1]["tags"][-1]
        environments = [durable, cache]
        with mock.patch.object(cohort, "restic_environment", return_value={}), mock.patch.object(
            cohort, "_inventory", side_effect=environments
        ):
            plan = cohort.plan_retention(now)
        self.assertIn(durable[0]["id"], plan["retain_durable"])
        self.assertIn(cache[0]["id"], plan["retain_cache"])
        self.assertNotIn(cache[0]["id"], plan["delete_cache"])

    def test_retention_refuses_orphaned_retained_durable_snapshot(self) -> None:
        now = datetime(2026, 9, 3, 12, tzinfo=UTC)
        durable = [{
            "id": "a" * 64,
            "time": now.isoformat(),
            "tags": [
                "usl-cohort", "durable", "target-production", "recovery-eligible",
                "run-20260903t120000z-deadbeef",
            ],
        }]
        with mock.patch.object(cohort, "restic_environment", return_value={}), mock.patch.object(
            cohort, "_inventory", side_effect=[durable, []]
        ), self.assertRaisesRegex(cohort.CohortError, "no unique cache"):
            cohort.plan_retention(now)

    def test_previous_generation_identity_rejects_paths_not_derived_from_state(self) -> None:
        target = mock.Mock(value={
            "state_directory": "/var/lib/usl-odoo/runtime/production",
            "compose": {"resource_overlay": None},
            "volumes": {"odoo_postgres": {}},
        })
        active = {
            "generation": "gcandidate",
            "volumes": {"odoo_postgres": "candidate-db"},
            "network": "candidate-network",
            "release_manifest": "/var/lib/usl-odoo/runtime/production/generations/gcandidate/usl-release.json",
            "snapshot": "a" * 64,
            "previous": {
                "generation": "gprevious",
                "volumes": {"odoo_postgres": "previous-db"},
                "network": "previous-network",
                "release_manifest": "/tmp/untrusted.json",
                "snapshot": "b" * 64,
            },
        }
        current = {
            "compose": {
                "compose_files": [
                    "/gitops/compose.yaml",
                    "/var/lib/usl-odoo/runtime/production/generations/gcandidate/compose.generation.json",
                ],
            },
            "active_state": active,
        }
        with self.assertRaisesRegex(RuntimeError, "manifest path"):
            _previous_generation_identity(target, mock.Mock(), current)

    def test_release_abort_restores_adopted_runtime_and_proves_it(self) -> None:
        target = mock.Mock()
        target.name = "production"
        target.value = {
                "state_directory": "/var/lib/usl-odoo/runtime/production",
                "compose": {"resource_overlay": None},
                "volumes": {"odoo_postgres": {}},
                "services": {"odoo": "odoo", "odoo_db": "db"},
            }
        candidate_file = "/var/lib/usl-odoo/runtime/production/generations/gcandidate/compose.generation.json"
        current = {
            "compose": {
                "project": "usl-odoo-production-main",
                "working_directory": "/gitops",
                "environment_file": "/run/prod.env",
                "profiles": [],
                "compose_files": ["/gitops/compose.yaml", candidate_file],
            },
            "active_state": {
                "generation": "gcandidate",
                "volumes": {"odoo_postgres": "candidate-db"},
                "network": "candidate-network",
                "release_manifest": "/var/lib/usl-odoo/runtime/production/generations/gcandidate/usl-release.json",
                "snapshot": "a" * 64,
                "previous": {
                    "generation": "adopted",
                    "volumes": {"odoo_postgres": "legacy-db"},
                    "network": None,
                    "release_manifest": None,
                    "snapshot": None,
                },
            },
        }

        class Runner:
            def __init__(self):
                self.commands = []

            def run(self, command, *, check=True, input_text=None):
                self.commands.append(command)
                return subprocess.CompletedProcess(command, 0, "", "")

        runner = Runner()
        with mock.patch("operations.stack.inspect_runtime", return_value=current), mock.patch(
            "operations.stack._gate",
            side_effect=[{"status": "passed"}, {"status": "passed"}],
        ):
            result = _abort_to_previous_generation(target, runner, TARGETS)
        self.assertEqual(result["status"], "rolled-back")
        up = next(command for command in runner.commands if command[-3:] == ["up", "--detach", "--wait"])
        self.assertNotIn(candidate_file, up)
        self.assertIn(
            ["rm", "-f", "/var/lib/usl-odoo/runtime/production/active.json"],
            runner.commands,
        )

    def test_candidate_cron_policy_is_applied_through_noninteractive_odoo_shell(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            policy_path = Path(directory) / "cron-policy.json"
            policy_path.write_text(json.dumps({
                "schema": "usl-production-cron-policy-v1",
                "gates": ["always"],
                "crons": {
                    "base.autovacuum_job": {
                        "gate": "always",
                        "reason": "maintenance",
                    },
                },
            }), encoding="utf-8")
            target = mock.Mock(value={
                "cron_policy": {
                    "mode": "managed",
                    "path": str(policy_path),
                    "gates": {"always": True},
                },
                "databases": {
                    "odoo": {
                        "service": "candidate-db",
                        "user": "odoo",
                        "name": "candidate",
                    },
                },
                "secrets": {"env_file": "/run/secrets/operations.env"},
            })

            class Runner:
                command = None
                input_text = None

                def run(self, command, *, check=True, input_text=None):
                    self.command = command
                    self.input_text = input_text
                    return subprocess.CompletedProcess(
                        command,
                        0,
                        'USL_CRON_POLICY_RESULT={"schema":"usl-cron-policy-application/v1","status":"applied"}\n',
                        "",
                    )

            runner = Runner()
            result = _apply_generation_cron_policy(
                target,
                runner,
                {"components": {"distribution": {"digest_reference": "odoo@sha256:" + "a" * 64}}},
                "candidate-network",
                {"odoo_filestore": "candidate-filestore"},
            )
            self.assertEqual(result["status"], "applied")
            self.assertIn("--interactive", runner.command)
            self.assertIn("odoo", runner.command)
            self.assertIn("base.autovacuum_job", runner.input_text)

    def _sign_secrets(self, root: Path) -> Path:
        for relative in cohort.SIGN_SECRET_FILES:
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            value = '{"key":"value"}' if relative.endswith(("ca.json", "provisioner.jwk")) else "material"
            path.write_text(value, encoding="utf-8")
        database = root / "step-ca/db"
        database.mkdir(parents=True, exist_ok=True)
        (database / "MANIFEST").write_text("state", encoding="utf-8")
        for directory in [root, *(path for path in root.rglob("*") if path.is_dir())]:
            directory.chmod(0o700)
        for relative in cohort.PRIVATE_SIGN_SECRET_FILES:
            (root / relative).chmod(0o600)
        return root

    def test_repository_initialization_refuses_authentication_or_network_failure(self) -> None:
        probe = subprocess.CompletedProcess(
            ["restic", "cat", "config"],
            1,
            "",
            "access denied",
        )
        with (
            mock.patch.object(cohort.subprocess, "run", return_value=probe),
            mock.patch.object(cohort, "run") as initialize,
            self.assertRaisesRegex(cohort.CohortError, "refusing to initialize"),
        ):
            cohort.ensure_repository({})
        initialize.assert_not_called()

    def test_repository_initialization_only_handles_missing_repository(self) -> None:
        probe = subprocess.CompletedProcess(["restic", "cat", "config"], 10, "", "missing")
        with (
            mock.patch.object(cohort.subprocess, "run", return_value=probe),
            mock.patch.object(cohort, "run") as initialize,
        ):
            cohort.ensure_repository({})
        initialize.assert_called_once_with(["restic", "init"], environment={}, capture=True)

    def test_restore_refuses_a_release_that_differs_from_the_cohort(self) -> None:
        release = {"schema": "usl-release/v2", "source": {"commit": "a" * 40}}
        materialized = {
            "release": {"commit": "a" * 40, "manifest_sha256": "b" * 64},
        }
        _validate_materialized_release(materialized, release, "b" * 64)
        materialized["release"]["manifest_sha256"] = "c" * 64
        with self.assertRaisesRegex(RuntimeError, "differs from the cohort"):
            _validate_materialized_release(materialized, release, "b" * 64)

    def test_v3_candidate_may_differ_from_verified_snapshot_release(self) -> None:
        release = {"schema": "usl-release/v3", "source": {"commit": "b" * 40}}
        materialized = {
            "release": {"commit": "a" * 40, "manifest_sha256": "c" * 64},
        }
        _validate_materialized_release(materialized, release, "d" * 64)

    def test_production_restore_requires_complete_sign_secrets(self) -> None:
        release = {"source": {"commit": "a" * 40}}
        materialized = {
            "release": {"commit": "a" * 40, "manifest_sha256": "b" * 64},
            "sign_secrets_restored": False,
        }
        with self.assertRaisesRegex(RuntimeError, "lacks complete Sign"):
            _validate_materialized_release(
                materialized,
                release,
                "b" * 64,
                require_sign_secrets=True,
            )

    def test_tree_identity_detects_content_change(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "folder").mkdir()
            file = root / "folder/document.pdf"
            file.write_bytes(b"one")
            before = cohort.tree_identity(root)
            file.write_bytes(b"two")
            after = cohort.tree_identity(root)
            self.assertEqual(before["files"], after["files"])
            self.assertNotEqual(before["sha256"], after["sha256"])

    def test_manifest_requires_exact_ollama_and_database_identity(self) -> None:
        empty = {"files": 0, "bytes": 0, "sha256": cohort.tree_identity(Path("/missing"))["sha256"]}
        value = manifest(empty, empty)
        self.assertEqual(cohort.validate_manifest(value)["schema"], cohort.SCHEMA)
        value["ollama"]["dimension"] = 768
        with self.assertRaisesRegex(cohort.CohortError, "1024"):
            cohort.validate_manifest(value)

    def test_complete_sign_secret_validation_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = self._sign_secrets(Path(temporary) / "sign")
            self.assertGreater(cohort.validate_sign_secrets(root)["files"], 1)
            (root / "odoo/provisioner.jwk").unlink()
            with self.assertRaisesRegex(cohort.CohortError, "provisioner.jwk"):
                cohort.validate_sign_secrets(root)

    def test_complete_sign_secret_validation_rejects_unsafe_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = self._sign_secrets(Path(temporary) / "sign")
            (root / "offline-root/root_ca_key").chmod(0o640)
            with self.assertRaisesRegex(cohort.CohortError, "unsafe permissions"):
                cohort.validate_sign_secrets(root)

    def test_complete_sign_secret_validation_rejects_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = self._sign_secrets(Path(temporary) / "sign")
            (root / "unsafe-link").symlink_to(root / "odoo/client.key")
            with self.assertRaisesRegex(cohort.CohortError, "symlink"):
                cohort.validate_sign_secrets(root)

    def test_legacy_manifest_can_be_verified_but_not_newly_qualified_for_production(self) -> None:
        empty = cohort.tree_identity(Path("/missing"))
        value = manifest(empty, empty)
        value["schema"] = cohort.LEGACY_SCHEMA
        value["resources"].pop("sign_secrets")
        self.assertEqual(cohort.validate_manifest(value)["schema"], cohort.LEGACY_SCHEMA)
        with (
            mock.patch.object(
                cohort,
                "verify",
                return_value={
                    "schema": cohort.STATE_SCHEMA,
                    "cohort_schema": cohort.LEGACY_SCHEMA,
                    "run_id": "legacy-run",
                    "target": "production",
                    "durable_snapshot_id": "1" * 64,
                    "cache_snapshot_id": "2" * 64,
                    "status": "verified",
                },
            ),
            self.assertRaisesRegex(cohort.CohortError, "legacy production snapshot"),
        ):
            cohort.qualify(argparse.Namespace(root="/cohort", durable_snapshot="1" * 64))

    def test_push_uploads_cache_first_and_excludes_it_from_durable_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_id = "20260901t120000z-a1b2c3d4"
            run_root = root / run_id
            (run_root / "durable").mkdir(parents=True)
            (run_root / "cache").mkdir()
            (run_root / "durable/item").write_text("durable", encoding="utf-8")
            (run_root / "cache/item").write_text("cache", encoding="utf-8")
            value = manifest(
                cohort.tree_identity(run_root / "durable"),
                cohort.tree_identity(run_root / "cache"),
            )
            (run_root / "manifest.json").write_text(json.dumps(value), encoding="utf-8")
            snapshots = iter(("1" * 64, "2" * 64))
            calls = []

            def fake_backup(paths, environment, tags):
                calls.append(([path.name for path in paths], tags))
                return next(snapshots)

            arguments = argparse.Namespace(root=str(root), run_id=run_id)
            with (
                mock.patch.object(cohort, "restic_environment", return_value={}),
                mock.patch.object(cohort, "restic_backup", side_effect=fake_backup),
            ):
                result = cohort.push(arguments)
            self.assertEqual(calls[0][0], ["cache"])
            self.assertEqual(calls[1][0], ["durable", "manifest.json"])
            self.assertIn("pending-verification", calls[1][1])
            self.assertNotIn("recovery-eligible", calls[1][1])
            self.assertEqual(result["cache_snapshot_id"], "1" * 64)
            self.assertEqual(result["durable_snapshot_id"], "2" * 64)

    def test_push_resumes_without_duplicate_upload_after_state_is_written(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_id = "20260901t120000z-a1b2c3d4"
            run_root = root / run_id
            run_root.mkdir()
            state = {
                "schema": cohort.STATE_SCHEMA,
                "run_id": run_id,
                "target": "production",
                "durable_snapshot_id": "1" * 64,
                "cache_snapshot_id": "2" * 64,
                "status": "uploaded",
            }
            (run_root / "state.json").write_text(json.dumps(state), encoding="utf-8")
            with mock.patch.object(cohort, "restic_backup") as backup:
                self.assertEqual(
                    cohort.push(argparse.Namespace(root=str(root), run_id=run_id)),
                    state,
                )
            backup.assert_not_called()

    def test_qualification_tags_snapshots_only_after_verification(self) -> None:
        arguments = argparse.Namespace(root="/cohort", durable_snapshot="1" * 64)
        calls = []
        with (
            mock.patch.object(
                cohort,
                "verify",
                return_value={
                    "schema": cohort.STATE_SCHEMA,
                    "cohort_schema": cohort.SCHEMA,
                    "run_id": "run-id",
                    "target": "production",
                    "durable_snapshot_id": "1" * 64,
                    "cache_snapshot_id": "2" * 64,
                    "status": "verified",
                },
            ) as verify,
            mock.patch.object(cohort, "restic_environment", side_effect=({"repo": "d"}, {"repo": "c"})),
            mock.patch.object(
                cohort,
                "retry_restic",
                side_effect=lambda command, environment: calls.append((command, environment)),
            ),
            mock.patch.object(
                cohort,
                "resolve_tagged_snapshot",
                return_value="3" * 64,
            ) as resolve,
        ):
            result = cohort.qualify(arguments)
        verify.assert_called_once_with(arguments)
        self.assertEqual(result["status"], "qualified")
        self.assertEqual(len(calls), 1)
        self.assertTrue(all("recovery-eligible" in command for command, _environment in calls))
        resolve.assert_called_once()
        self.assertEqual(result["durable_snapshot_id"], "3" * 64)
        self.assertEqual(result["cache_snapshot_id"], "2" * 64)

    def test_qualified_snapshot_resolution_requires_one_exact_tag_match(self) -> None:
        required = {"usl-cohort", "durable", "recovery-eligible", "run-example"}
        inventory = [
            {"id": "1" * 64, "tags": sorted(required)},
            {"id": "2" * 64, "tags": ["usl-cohort", "durable"]},
        ]
        completed = subprocess.CompletedProcess(
            ["restic", "snapshots"],
            0,
            json.dumps(inventory),
            "",
        )
        with mock.patch.object(cohort, "retry_restic", return_value=completed):
            self.assertEqual(cohort.resolve_tagged_snapshot({}, required), "1" * 64)

    def test_snapshot_reference_follows_one_rewritten_tag_identity(self) -> None:
        original = "1" * 64
        current = "2" * 64
        inventory = [{"id": current, "original": original, "tags": ["recovery-eligible"]}]
        completed = subprocess.CompletedProcess(
            ["restic", "snapshots"],
            0,
            json.dumps(inventory),
            "",
        )
        with mock.patch.object(cohort, "retry_restic", return_value=completed):
            self.assertEqual(cohort.resolve_snapshot_reference({}, original), current)

    def test_staging_restore_isolates_production_mcp_oauth_state(self) -> None:
        self.assertFalse(cohort.should_restore_resource("mcp_oauth", "staging"))
        self.assertFalse(cohort.should_restore_resource("mcp_oauth", "local"))
        self.assertTrue(cohort.should_restore_resource("mcp_oauth", "production"))
        self.assertFalse(cohort.should_restore_resource("sign_secrets", "staging"))
        self.assertFalse(cohort.should_restore_resource("sign_secrets", "local"))
        self.assertTrue(cohort.should_restore_resource("sign_secrets", "production"))
        self.assertTrue(cohort.should_restore_resource("paperless_originals", "staging"))

    def test_container_mounts_every_durable_and_cache_source_read_only(self) -> None:
        target = load_target("production", TARGETS)
        command = _cohort_command(target, "backup@sha256:" + "a" * 64, "capture", [])
        joined = " ".join(command)
        for role in (
            "odoo_filestore",
            "paperless_media",
            "paperless_data",
            "paperless_trash",
            "paperless_consume",
            "mcp_oauth",
        ):
            self.assertIn(target.value["volumes"][role]["name"], joined)
        self.assertIn(target.value["paths"]["sign_secrets"]["path"] + ":/source/sign-secrets:ro", joined)
        self.assertGreaterEqual(joined.count(":ro"), 7)

    def test_backup_pause_perimeter_includes_step_ca(self) -> None:
        target = load_target("production", TARGETS)
        services = [target.value["services"][role] for role in BACKUP_WRITER_SERVICE_ROLES]
        self.assertIn("usl-sign-step-ca", services)
        self.assertIn("usl-sign-dss", services)

    def test_writer_start_is_attempted_after_capture_failure(self) -> None:
        identity = {
            "project": "safe-project",
            "working_directory": "/release",
            "environment_file": "/runtime.env",
            "compose_files": ["/release/compose.yaml"],
        }

        class RecordingRunner:
            def __init__(self):
                self.commands = []

            def run(self, command, *, check=True):
                self.commands.append(command)
                return subprocess.CompletedProcess(command, 0, "", "")

        runner = RecordingRunner()
        with self.assertRaisesRegex(RuntimeError, "injected"):
            with_writers_paused(
                runner,
                identity,
                ["odoo", "paperless"],
                lambda: (_ for _ in ()).throw(RuntimeError("injected")),
            )
        self.assertIn("stop", runner.commands[0])
        self.assertIn("30", runner.commands[0])
        self.assertIn("up", runner.commands[-1])
        self.assertIn("--no-recreate", runner.commands[-1])

    def test_successful_release_capture_can_leave_writers_quiesced(self) -> None:
        identity = {
            "project": "safe-project",
            "working_directory": "/release",
            "environment_file": "/runtime.env",
            "compose_files": ["/release/compose.yaml"],
        }

        class RecordingRunner:
            def __init__(self):
                self.commands = []

            def run(self, command, *, check=True):
                self.commands.append(command)
                return subprocess.CompletedProcess(command, 0, "", "")

        runner = RecordingRunner()
        result = with_writers_paused(
            runner,
            identity,
            ["odoo", "paperless"],
            lambda: {"status": "captured"},
            resume_after_success=False,
        )
        self.assertEqual(result["status"], "captured")
        self.assertIn("stop", runner.commands[0])
        self.assertEqual(len(runner.commands), 1)

    def test_backup_image_is_pulled_only_when_missing(self) -> None:
        image = "backup@sha256:" + "a" * 64

        class RecordingRunner:
            def __init__(self, present: bool):
                self.present = present
                self.commands = []

            def run(self, command, *, check=True):
                self.commands.append(command)
                status = 0 if self.present or command[:2] != ["docker", "image"] else 1
                return subprocess.CompletedProcess(command, status, "", "")

        present = RecordingRunner(True)
        _ensure_image(present, image)
        self.assertEqual(len(present.commands), 1)
        missing = RecordingRunner(False)
        _ensure_image(missing, image)
        self.assertEqual(missing.commands[-1], ["docker", "pull", image])

    def test_restore_prepulls_every_release_image_once(self) -> None:
        references = [
            f"ghcr.io/unstaticlabs/image-{index}@sha256:{str(index) * 64}"
            for index in range(1, 7)
        ]
        release = {
            "components": {
                "backup-tool": {"digest_reference": references[0]},
                "distribution": {"digest_reference": references[1]},
                "paperless": {"digest_reference": references[2]},
                "sign-dss": {"digest_reference": references[3]},
            },
            "mcp": {"image": references[4]},
            "renderer": {"image": references[5]},
        }
        self.assertEqual(_release_images(release), sorted(references))

    def test_backup_refuses_runtime_images_outside_selected_release(self) -> None:
        target = load_target("staging", TARGETS)
        reference = "ghcr.io/unstaticlabs/example@sha256:" + "a" * 64
        release = {
            "components": {
                "distribution": {"digest_reference": reference},
                "paperless": {"digest_reference": reference},
                "sign-dss": {"digest_reference": reference},
            },
            "mcp": {"image": reference},
            "renderer": {"image": reference},
        }
        runtime = {
            "containers": [
                {"Service": target.value["services"][key], "ID": key, "State": "running"}
                for key in ("odoo", "paperless", "sign", "mcp", "renderer")
            ],
        }

        class ImageRunner:
            def run(self, command, *, check=True):
                output = (
                    "sha256:expected\n"
                    if command[:3] == ["docker", "image", "inspect"]
                    else "sha256:actual\n"
                )
                return subprocess.CompletedProcess(command, 0, output, "")

        with self.assertRaisesRegex(RuntimeError, "running distribution image differs"):
            _validate_runtime_release_images(target, ImageRunner(), runtime, release)

    def test_backup_recovers_a_pruned_named_reference_before_comparing_images(self) -> None:
        target = load_target("staging", TARGETS)
        reference = "ghcr.io/unstaticlabs/example@sha256:" + "a" * 64
        release = {
            "components": {
                "distribution": {"digest_reference": reference},
                "paperless": {"digest_reference": reference},
                "sign-dss": {"digest_reference": reference},
            },
            "mcp": {"image": reference},
            "renderer": {"image": reference},
        }
        runtime = {
            "containers": [
                {"Service": target.value["services"][key], "ID": key, "State": "running"}
                for key in ("odoo", "paperless", "sign", "mcp", "renderer")
            ],
        }

        class PrunedReferenceRunner:
            def __init__(self):
                self.pulled = False

            def run(self, command, *, check=True):
                if command[:2] == ["docker", "inspect"]:
                    return subprocess.CompletedProcess(command, 0, "sha256:local-image\n", "")
                if command[-1] == "{{.Id}}":
                    status = 0 if self.pulled else 1
                    return subprocess.CompletedProcess(
                        command,
                        status,
                        "sha256:local-image\n" if self.pulled else "",
                        "" if self.pulled else "No such image",
                    )
                self.pulled = True
                return subprocess.CompletedProcess(command, 0, "", "")

        runner = PrunedReferenceRunner()
        verified = _validate_runtime_release_images(
            target,
            runner,
            runtime,
            release,
        )
        self.assertEqual(set(verified.values()), {reference})
        self.assertTrue(runner.pulled)

    def test_restore_capacity_fails_closed_below_two_gibibytes(self) -> None:
        target = load_target("staging", TARGETS)

        class CapacityRunner:
            def run(self, command, *, check=True):
                return subprocess.CompletedProcess(command, 0, "Avail\n1073741824\n", "")

        with self.assertRaisesRegex(RuntimeError, "below the 2 GiB safety floor"):
            _require_restore_capacity(target, CapacityRunner(), "preflight")

    def test_restore_capacity_uses_measured_candidate_and_reserve(self) -> None:
        target = load_target("staging", TARGETS)

        class CapacityRunner:
            def __init__(self, available):
                self.available = available

            def run(self, command, *, check=True):
                return subprocess.CompletedProcess(
                    command, 0, f"Avail\n{self.available}\n", "",
                )

        gib = 1024**3
        with self.assertRaisesRegex(RuntimeError, "2.0 GiB deficit"):
            _require_restore_capacity(
                target,
                CapacityRunner(17 * gib),
                "preflight",
                candidate_bytes=4 * gib,
            )
        admitted = _require_restore_capacity(
            target,
            CapacityRunner(19 * gib),
            "preflight",
            candidate_bytes=4 * gib,
        )
        self.assertEqual(admitted["required_bytes"], 19 * gib)

    def test_candidate_measurement_counts_unique_volumes_and_required_paths(self) -> None:
        target = load_target("staging", TARGETS)
        runtime = {
            "volumes": {
                "one": {"name": "volume-a"},
                "duplicate": {"name": "volume-a"},
                "two": {"name": "volume-b"},
            },
        }

        class MeasurementRunner:
            def run(self, command, *, check=True):
                if command[:2] == ["docker", "run"]:
                    return subprocess.CompletedProcess(command, 0, "1024\t/source\n", "")
                return subprocess.CompletedProcess(command, 0, "512\t/path\n", "")

        # Two unique volumes plus both durable Sign paths. A missing optional
        # path is ignored by the real runner; this fixture reports both.
        self.assertEqual(
            _measure_candidate_bytes(target, MeasurementRunner(), "tool@sha256:" + "a" * 64, runtime),
            3072,
        )

    def test_materialization_workspace_cleanup_is_exactly_scoped(self) -> None:
        target = load_target("staging", TARGETS)

        class RecordingRunner:
            def __init__(self):
                self.commands = []

            def run(self, command, *, check=True):
                self.commands.append(command)
                return subprocess.CompletedProcess(command, 0, "", "")

        runner = RecordingRunner()
        _remove_materialization_workspace(target, runner, "g20260901-a1b2c3d4")
        self.assertEqual(
            runner.commands,
            [[
                "rm",
                "-rf",
                target.value["state_directory"] + "/generations/g20260901-a1b2c3d4/work",
            ]],
        )

    def test_fresh_odoo_volume_is_prepared_for_the_runtime_user(self) -> None:
        image = "ghcr.io/unstaticlabs/usl-odoo@sha256:" + "a" * 64
        release = {"components": {"distribution": {"digest_reference": image}}}

        class RecordingRunner:
            def __init__(self):
                self.commands = []

            def run(self, command, *, check=True):
                self.commands.append(command)
                return subprocess.CompletedProcess(command, 0, "", "")

        runner = RecordingRunner()
        _prepare_generation_volume_ownership(
            runner,
            release,
            {"odoo_filestore": "fresh-odoo-data"},
        )
        command = runner.commands[0]
        self.assertIn("0:0", command)
        self.assertIn("fresh-odoo-data:/var/lib/odoo", command)
        self.assertIn(image, command)
        self.assertEqual(command[-3:], ["1000:1000", "/var/lib/odoo", "/var/lib/odoo/filestore"])

    def test_rollback_failure_preserves_the_activation_error(self) -> None:
        identity = {
            "project": "safe-project",
            "working_directory": "/release",
            "environment_file": "/runtime.env",
            "compose_files": ["/release/compose.yaml"],
        }

        class FailedRunner:
            def run(self, command, *, check=True):
                return subprocess.CompletedProcess(command, 1, "", "disk full")

        with self.assertRaisesRegex(RuntimeError, "activation failed \\(original failure\\).*disk full"):
            _rollback_after_failure(FailedRunner(), identity, RuntimeError("original failure"))

    def test_rollback_reactivates_the_previous_generation_overlay(self) -> None:
        identity = {
            "project": "safe-project",
            "working_directory": "/release",
            "environment_file": "/runtime.env",
            "compose_files": [
                "/release/compose.yaml",
                "/runtime/generations/g-previous/compose.generation.json",
            ],
        }

        class RecordingRunner:
            def __init__(self):
                self.commands = []

            def run(self, command, *, check=True):
                self.commands.append(command)
                return subprocess.CompletedProcess(command, 0, "", "")

        runner = RecordingRunner()
        _rollback_after_failure(runner, identity, RuntimeError("new generation failed"))
        command = runner.commands[0]
        self.assertIn(
            "/runtime/generations/g-previous/compose.generation.json",
            command,
        )
        self.assertEqual(command[-3:], ["up", "--detach", "--wait"])
        self.assertNotIn("--force-recreate", command)

    def test_runtime_lock_is_released_after_failure(self) -> None:
        target = load_target("local", TARGETS)

        class RecordingRunner:
            def __init__(self):
                self.commands = []

            def run(self, command, *, check=True):
                self.commands.append(command)
                return subprocess.CompletedProcess(command, 0, "", "")

        runner = RecordingRunner()
        with self.assertRaisesRegex(RuntimeError, "injected"):
            with runtime_lock(target, runner, "backup", "run-id"):
                raise RuntimeError("injected")
        self.assertIn(["rmdir", target.value["state_directory"] + "/operation.lock"], runner.commands)

    def test_production_restore_requires_exact_confirmation_before_runtime_access(self) -> None:
        arguments = argparse.Namespace(
            source="production",
            target="production",
            targets=TARGETS,
            replace=False,
            confirm=None,
        )
        with self.assertRaisesRegex(RuntimeError, "protected restore"):
            _restore_unlocked(arguments)

    def test_generation_uses_unique_exact_volume_names(self) -> None:
        target = load_target("staging", TARGETS)
        names = generation_volume_names(target, "g20260901-a1b2c3d4")
        self.assertEqual(set(names), set(target.value["volumes"]))
        self.assertEqual(len(set(names.values())), len(names))
        self.assertTrue(all("g20260901-a1b2c3d4" in name for name in names.values()))
        overlay = json.loads(_generation_overlay(names))
        self.assertEqual(set(overlay["volumes"]), set(VOLUME_LOGICAL_NAMES.values()))
        self.assertTrue(all(value["external"] for value in overlay["volumes"].values()))

    def test_generation_pins_every_release_owned_runtime_image(self) -> None:
        digest = "sha256:" + "a" * 64
        reference = "ghcr.io/unstaticlabs/example@" + digest
        release = {
            "components": {
                "distribution": {"digest_reference": reference},
                "paperless": {"digest_reference": reference},
                "sign-dss": {"digest_reference": reference},
            },
            "mcp": {"image": reference},
            "renderer": {"image": reference},
        }
        target = load_target("staging", TARGETS)
        names = generation_volume_names(target, "g20260901-a1b2c3d4")
        services = {
            "odoo",
            "paperless-webserver",
            "usl-sign-dss",
            "odoo-mcp",
            "usl-document-renderer",
        }
        overlay = json.loads(
            _generation_overlay(names, release, services, target.value["ingress"]),
        )
        self.assertEqual(set(overlay["services"]), services)
        self.assertTrue(all(item["image"] == reference for item in overlay["services"].values()))
        self.assertEqual(
            overlay["services"]["odoo"]["environment"],
            {
                "ODOO_PROXY_MODE": "True",
                "ODOO_LIST_DB": "False",
                "ODOO_DB_FILTER": "^odoo_staging$",
            },
        )

    def test_production_generation_activates_restored_sign_secrets(self) -> None:
        target = load_target("production", TARGETS)
        names = generation_volume_names(target, "g20260902-a1b2c3d4")
        root = "/var/lib/usl-odoo/runtime/production/generations/g20260902-a1b2c3d4/sign-secrets"
        overlay = json.loads(
            _generation_overlay(
                names,
                sign_secret_root=root,
                service_names=target.value["services"],
            ),
        )
        services = overlay["services"]
        self.assertIn(root + "/step-ca:/home/step", services["usl-sign-step-ca"]["volumes"])
        self.assertIn(root + "/dss:/run/usl-sign-dss:ro", services["usl-sign-dss"]["volumes"])
        self.assertIn(root + "/odoo:/run/usl-sign:ro", services["odoo"]["volumes"])
        self.assertTrue(services["usl-sign-step-ca"]["env_file"][0]["required"])
        self.assertTrue(services["usl-sign-dss"]["env_file"][0]["required"])
        self.assertTrue(services["odoo"]["env_file"][0]["required"])

    def test_materialization_uses_source_repositories_and_fresh_target_volumes(self) -> None:
        source = load_target("production", TARGETS)
        target = load_target("staging", TARGETS)
        names = generation_volume_names(target, "g20260901-a1b2c3d4")
        command = _materialize_command(
            source,
            target,
            "backup@sha256:" + "a" * 64,
            "b" * 64,
            "g20260901-a1b2c3d4",
            "recovery-network",
            names,
        )
        joined = " ".join(command)
        self.assertIn(source.value["backup"]["durable_repository"], joined)
        self.assertIn(source.value["backup"]["cache_repository"], joined)
        self.assertIn(names["odoo_filestore"] + ":/target/odoo-data", joined)
        self.assertIn("/sign-secrets:/target/sign-secrets", joined)
        self.assertIn("USL_RESTORE_GENERATION_CONFIRMED=g20260901-a1b2c3d4", joined)
        self.assertIn("USL_TARGET_ENVIRONMENT=staging", joined)


if __name__ == "__main__":
    unittest.main()
