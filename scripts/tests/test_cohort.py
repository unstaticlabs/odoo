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
    _create_generation_resources,
    _apply_generation_cron_policy,
    _active_generation_identity,
    _adopt_staging_gateway,
    _abort_to_previous_generation,
    _activate_generation,
    _candidate_compose_identity,
    _cleanup_inventory,
    _cleanup_workspaces,
    _delete_cleanup_resources,
    _generation_overlay,
    _ensure_image,
    _materialize_command,
    _materialization_cleanup,
    _prepare_generation_volume_ownership,
    _previous_generation_record,
    _previous_generation_identity,
    _reconcile_staging_pocketid,
    _release_images,
    _remove_materialization_workspace,
    _resource_overlay,
    _require_restore_capacity,
    _measure_candidate_bytes,
    _notify_release,
    _rollback_after_failure,
    _start_rollback_identity,
    _run_candidate_upgrade,
    _restore_unlocked,
    _storage_status,
    _write_adopt_generation,
    _validate_materialized_release,
    _validate_runtime_release_images,
    _probe_staging_gateway_maintenance,
    _validated_cleanup_resources,
    _validated_cleanup_network,
    _validated_cleanup_volume,
    cleanup_command,
    generation_volume_names,
    generation_volume_path,
    runtime_lock,
    with_writers_paused,
)


ROOT = Path(__file__).resolve().parents[2]
TARGETS = ROOT / "operations/targets"
HOST_TARGETS = ROOT / "operations/targets-host"


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
    def test_storage_status_rejects_running_service_on_legacy_volume(self) -> None:
        target = mock.Mock(
            name="production",
            value={
                "environment": "local",
                "services": {"odoo_db": "db"},
                "storage": {
                    "tiers": {
                        "bulk": {"path": "/srv/storage"},
                        "database": {"path": "/srv/db"},
                    },
                },
                "volumes": {
                    "odoo_postgres": {"tier": "database"},
                },
            },
        )
        target.name = "production"
        expected = "/srv/db/usl-odoo/production/generations/g20260903-a/odoo_postgres"

        class StatusRunner:
            def run(self, command, *, check=True):
                if command[0] == "findmnt":
                    source = "/dev/volume" if command[2] == "/srv/storage" else "/dev/root"
                    return subprocess.CompletedProcess(
                        command, 0, f"{source} ext4 uuid {command[2]}\n", "",
                    )
                if command[:3] == ["docker", "volume", "inspect"]:
                    return subprocess.CompletedProcess(
                        command,
                        0,
                        json.dumps(
                            {
                                "Options": {"device": expected, "o": "bind", "type": "none"},
                                "Mountpoint": "/srv/storage/docker/volumes/active/_data",
                            },
                        ),
                        "",
                    )
                if command[:2] == ["docker", "inspect"]:
                    return subprocess.CompletedProcess(
                        command,
                        0,
                        json.dumps(
                            [
                                {
                                    "Type": "volume",
                                    "Name": "usl-odoo-production-main-postgres",
                                },
                            ],
                        ),
                        "",
                    )
                if command[:2] == ["docker", "info"]:
                    return subprocess.CompletedProcess(command, 0, "/var/lib/docker\n", "")
                raise AssertionError(command)

        result = _storage_status(
            target,
            StatusRunner(),
            {
                "generation": "g20260903-a",
                "containers": [{"Service": "db", "Name": "db-1", "State": "running"}],
                "volumes": {
                    "odoo_postgres": {"name": "active", "tier": "database"},
                },
            },
        )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["volumes"]["odoo_postgres"]["runtime_status"], "wrong-runtime-volume")
        self.assertIn("running service does not mount the active volume: db/odoo_postgres", result["failures"])

    def test_adoption_reads_the_recorded_active_release_manifest(self) -> None:
        target = mock.Mock(value={
            "state_directory": "/var/lib/usl-odoo/runtime/staging",
            "compose": {"resource_overlay": None},
            "ingress": {},
            "services": {"odoo": "odoo-staging"},
        })
        runner = mock.Mock()
        runner.run.return_value = subprocess.CompletedProcess(
            [], 0, json.dumps({"schema": "usl-release/v3"}), "",
        )
        with mock.patch("operations.stack._write_remote"), mock.patch(
            "operations.stack._runtime_images", return_value={}
        ), mock.patch("operations.stack._generation_overlay", return_value="{}"):
            _write_adopt_generation(
                target,
                runner,
                {"compose_files": ["/compose.yaml"]},
                "gstorage-active",
                {},
                "generation-network",
                "a" * 64,
                "/var/lib/usl-odoo/runtime/staging/generations/glegacy/usl-release.json",
            )
        self.assertIn(
            mock.call([
                "cat",
                "/var/lib/usl-odoo/runtime/staging/generations/glegacy/usl-release.json",
            ]),
            runner.run.call_args_list,
        )

    def test_active_generation_identity_uses_recorded_overlays_after_adoption(self) -> None:
        target = mock.Mock(value={
            "state_directory": "/var/lib/usl-odoo/runtime/production",
            "compose": {"resource_overlay": "compose.resources.production.json"},
        })
        generation = "gstorage-active"
        root = f"/var/lib/usl-odoo/runtime/production/generations/{generation}"
        current = {
            "compose": {
                "compose_files": [
                    "/gitops/compose.yaml",
                    "/var/lib/usl-odoo/runtime/production/generations/glegacy/compose.generation.json",
                ],
            },
            "active_state": {
                "generation": generation,
                "release_manifest": f"{root}/usl-release.json",
            },
        }
        runner = mock.Mock()
        runner.run.return_value = subprocess.CompletedProcess([], 0, "", "")
        identity = _active_generation_identity(target, runner, current)
        self.assertEqual(
            identity["compose_files"],
            [
                "/gitops/compose.yaml",
                f"{root}/compose.resources.json",
                f"{root}/compose.generation.json",
            ],
        )
        self.assertEqual(runner.run.call_count, 3)

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
            'USL_RELEASE_NOTIFICATION_RESULT={"channel": "usl_home.channel_distribution_updates", "message_id": 42, "release": "' + release_id + '", "status": "posted"}\n',
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
        self.assertIn("usl_home.channel_distribution_updates", program)
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

    def test_staging_abort_refuses_legacy_v2_without_exact_compose_identity(self) -> None:
        target = load_target("staging", HOST_TARGETS)
        generation = "g20260903-storage-a"
        root = f"{target.value['state_directory']}/generations/{generation}"
        current = {
            "compose": {
                "project": target.project,
                "working_directory": "/gitops/staging",
                "environment_file": "/runtime/staging.env",
                "profiles": [],
                "anchor_service": "odoo-staging",
                "compose_files": ["/gitops/staging/compose.yaml"],
            },
            "active_state": {
                "generation": "g20260904-v3",
                "previous": {
                    "generation": generation,
                    "volumes": {
                        role: f"legacy-{role}"
                        for role in target.value["volumes"]
                    },
                    "network": "legacy-network",
                    "release_manifest": f"{root}/usl-release.json",
                    "snapshot": "b" * 64,
                },
            },
        }

        class LegacyReleaseRunner:
            release = {"schema": "usl-release/v2"}

            def run(self, command, *, check=True):
                if command[:2] == ["test", "-f"]:
                    return subprocess.CompletedProcess(command, 0, "", "")
                if command[:1] == ["cat"]:
                    return subprocess.CompletedProcess(
                        command, 0, json.dumps(self.release), "",
                    )
                raise AssertionError(command)

        with self.assertRaisesRegex(RuntimeError, "lacks its exact Compose identity"):
            _previous_generation_identity(target, LegacyReleaseRunner(), current)

        malformed = LegacyReleaseRunner()
        malformed.release = {"schema": "usl-release/v3"}
        with self.assertRaisesRegex(RuntimeError, "rollback release manifest is invalid"):
            _previous_generation_identity(target, malformed, current)

    def test_staging_abort_admits_only_the_recorded_legacy_v2_compose_identity(self) -> None:
        target = load_target("staging", HOST_TARGETS)
        generation = "g20260903-storage-a"
        state_root = target.value["state_directory"]
        working = f"{state_root}/validation-8973f9ee903"
        release_path = f"{state_root}/generations/{generation}/usl-release.json"
        legacy = {
            "project": target.project,
            "working_directory": working,
            "environment_file": target.value["compose"]["adoption"]["candidate"]["environment_file"],
            "profiles": target.value["compose"]["profiles"],
            "anchor_service": "odoo",
            "compose_files": [
                f"{working}/compose.yaml",
                f"{working}/compose.resources.json",
                f"{working}/usl-staging-proxy-generation.json",
                f"{state_root}/generations/{generation}/compose.resources.json",
                f"{state_root}/generations/{generation}/compose.generation.json",
            ],
        }
        current = {
            "compose": {
                "project": target.project,
                "working_directory": "/gitops/staging",
                "environment_file": "/runtime/staging.env",
                "profiles": target.value["compose"]["profiles"],
                "anchor_service": "odoo-staging",
                "compose_files": ["/gitops/staging/compose.yaml"],
            },
            "active_state": {
                "generation": "gcandidate",
                "previous": {
                    "generation": generation,
                    "volumes": {role: f"legacy-{role}" for role in target.value["volumes"]},
                    "network": "legacy-network",
                    "release_manifest": release_path,
                    "snapshot": "b" * 64,
                    "compose": legacy,
                },
            },
        }

        class LegacyRunner:
            def run(self, command, *, check=True):
                if command[:2] in (["test", "-f"], ["test", "-d"]):
                    return subprocess.CompletedProcess(command, 0, "", "")
                if command[:2] == ["readlink", "-f"]:
                    return subprocess.CompletedProcess(command, 0, command[-1] + "\n", "")
                if command[:1] == ["cat"]:
                    return subprocess.CompletedProcess(command, 0, '{"schema":"usl-release/v2"}', "")
                if command[-2:] == ["config", "--services"]:
                    services = set(target.value["services"].values())
                    services.remove("odoo-staging")
                    services.add("odoo")
                    return subprocess.CompletedProcess(command, 0, "\n".join(sorted(services)) + "\n", "")
                raise AssertionError(command)

        with mock.patch("operations.stack.validate_release", return_value={"schema": "usl-release/v2"}):
            identity, state = _previous_generation_identity(target, LegacyRunner(), current)
        self.assertEqual(identity, legacy)
        self.assertEqual(json.loads(state)["generation"], generation)

        poisoned = json.loads(json.dumps(current))
        poisoned["active_state"]["previous"]["compose"]["compose_files"][0] = "/tmp/compose.yaml"
        with self.assertRaisesRegex(RuntimeError, "outside the validation perimeter"):
            _previous_generation_identity(target, LegacyRunner(), poisoned)

    def test_first_v3_state_records_exact_legacy_compose_identity_for_late_rollback(self) -> None:
        target = load_target("staging", HOST_TARGETS)
        legacy = {
            "container_id": "runtime-only",
            "project": target.project,
            "working_directory": target.value["state_directory"] + "/validation-8973f9ee903",
            "environment_file": target.value["compose"]["adoption"]["candidate"]["environment_file"],
            "profiles": target.value["compose"]["profiles"],
            "anchor_service": "odoo",
            "compose_files": [target.value["state_directory"] + "/validation-8973f9ee903/compose.yaml"],
        }
        current = {
            "generation": "g20260903-storage-a",
            "compose": legacy,
            "active_state": {
                "network": "legacy-network",
                "release_manifest": target.value["state_directory"] + "/generations/g20260903-storage-a/usl-release.json",
                "snapshot": "a" * 64,
            },
            "volumes": {
                role: {"name": f"legacy-{role}"}
                for role in target.value["volumes"]
            },
        }
        previous = _previous_generation_record(target, current)
        self.assertEqual(previous["compose"]["anchor_service"], "odoo")
        self.assertNotIn("container_id", previous["compose"])
        self.assertEqual(previous["compose"]["compose_files"], legacy["compose_files"])

    def test_post_activation_abort_removes_canonical_anchor_before_legacy_v2_restart(self) -> None:
        target = load_target("staging", HOST_TARGETS)
        current_identity = {
            "project": target.project,
            "working_directory": "/gitops/staging",
            "environment_file": "/runtime/staging.env",
            "profiles": [],
            "anchor_service": "odoo-staging",
            "compose_files": ["/gitops/staging/compose.yaml"],
        }
        legacy_identity = {
            **current_identity,
            "anchor_service": "odoo",
            "compose_files": ["/runtime/legacy-v2.json"],
        }
        current = {
            "compose": current_identity,
            "active_state": {"generation": "gv3"},
        }

        class Runner:
            def __init__(self):
                self.commands = []

            def run(self, command, *, check=True, input_text=None):
                self.commands.append(command)
                if command[:2] == ["test", "-f"]:
                    return subprocess.CompletedProcess(command, 0, "", "")
                if command[:2] == ["docker", "ps"]:
                    return subprocess.CompletedProcess(command, 0, "canonical-id\n", "")
                if command[:3] == ["docker", "inspect", "canonical-id"]:
                    return subprocess.CompletedProcess(
                        command,
                        0,
                        json.dumps({
                            "com.docker.compose.project": target.project,
                            "com.docker.compose.service": "odoo-staging",
                        }),
                        "",
                    )
                return subprocess.CompletedProcess(command, 0, "", "")

        runner = Runner()
        with mock.patch("operations.stack.inspect_runtime", return_value=current), mock.patch(
            "operations.stack._previous_generation_identity",
            return_value=(legacy_identity, '{"generation":"legacy"}'),
        ), mock.patch(
            "operations.stack._start_rollback_identity",
        ) as starter, mock.patch(
            "operations.stack._gate",
            side_effect=[{"status": "passed"}, {"status": "passed"}],
        ) as gates:
            result = _abort_to_previous_generation(target, runner, TARGETS)
        remove = ["docker", "rm", "--force", "canonical-id"]
        self.assertIn(remove, runner.commands)
        # The shared starter keeps the recovered legacy Odoo behind the stable gateway.
        starter.assert_called_once_with(target, runner, legacy_identity)
        self.assertEqual(gates.call_count, 2)
        self.assertEqual(result["status"], "rolled-back")

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
        policy_path = "/opt/usl/deploy/production.cron-policy.json"
        policy_raw = json.dumps({
                "schema": "usl-production-cron-policy-v1",
                "gates": ["always"],
                "crons": {
                    "base.autovacuum_job": {
                        "gate": "always",
                        "reason": "maintenance",
                    },
                },
            })
        target = mock.Mock(value={
                "cron_policy": {
                    "mode": "managed",
                    "path": policy_path,
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
                if command == ["cat", policy_path]:
                    return subprocess.CompletedProcess(
                        command, 0, policy_raw, "",
                    )
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

    def test_staging_upgrade_uses_approved_pocket_id_runtime_without_regulatory_access(self) -> None:
        target = load_target("staging", HOST_TARGETS)
        candidate = {
            "environment_file": target.value["compose"]["adoption"]["candidate"]["environment_file"],
        }
        release = {
            "identity": "a" * 64,
            "components": {
                "distribution": {
                    "digest_reference": "ghcr.io/unstaticlabs/usl-odoo@sha256:" + "b" * 64,
                },
            },
        }
        plan = {
            "schema": "usl-module-upgrade-plan/v1",
            "active_release": "c" * 64,
            "candidate_release": release["identity"],
            "upgrade_modules": ["usl_pocketid"],
            "changed_modules": ["usl_pocketid"],
            "reasons": {"usl_pocketid": ["source_sha256"]},
        }
        runner = mock.Mock()
        runner.run.return_value = subprocess.CompletedProcess([], 0, "", "")
        with mock.patch("operations.stack.validate_upgrade_plan", return_value=plan):
            _run_candidate_upgrade(
                target,
                runner,
                release,
                "candidate-network",
                {"odoo_filestore": "candidate-filestore"},
                plan,
                candidate,
            )
        command = runner.run.call_args.args[0]
        self.assertIn(candidate["environment_file"], command)
        self.assertIn("USL_EINVOICE_LIVE_ENABLED=0", command)
        self.assertIn("USL_EREPORTING_LIVE_ENABLED=0", command)
        self.assertIn("USL_POCKET_ID_ENABLED=1", command)
        shell = command[command.index("-c") + 1]
        self.assertIn("${POCKET_ID_CLIENT_SECRET:?}", shell)
        self.assertNotIn("client-secret-value", " ".join(command))

    def test_staging_pocket_id_reconcile_returns_redacted_runtime_evidence(self) -> None:
        target = load_target("staging", HOST_TARGETS)
        candidate = {
            "environment_file": target.value["compose"]["adoption"]["candidate"]["environment_file"],
        }
        release = {
            "components": {
                "distribution": {
                    "digest_reference": "ghcr.io/unstaticlabs/usl-odoo@sha256:" + "b" * 64,
                },
            },
        }
        checks = {
            "application_completed": True,
            "provider_enabled": True,
            "governed_provider": True,
            "client_id_matches": True,
            "database_secret_absent": True,
            "issuer_matches": True,
            "base_url_matches": True,
            "required_group_matches": True,
            "scopes_match": True,
            "endpoints_match_issuer": True,
        }
        evidence = {
            "schema": "usl-pocket-id-runtime-admission/v1",
            "status": "passed",
            **checks,
        }
        runner = mock.Mock()
        runner.run.return_value = subprocess.CompletedProcess(
            [],
            0,
            "USL_POCKET_ID_RUNTIME_ADMISSION=" + json.dumps(evidence) + "\n",
            "",
        )
        result = _reconcile_staging_pocketid(
            target,
            runner,
            release,
            "candidate-network",
            {"odoo_filestore": "candidate-filestore"},
            candidate,
        )
        self.assertEqual(result, evidence)
        self.assertNotIn("client_id", result)
        self.assertNotIn("client_secret", result)
        command = runner.run.call_args.args[0]
        self.assertLess(command.index("USL_POCKET_ID_ENABLED=1"), command.index("--entrypoint"))

    def test_staging_pocket_id_reconcile_rejects_unapproved_environment_and_failed_check(self) -> None:
        target = load_target("staging", HOST_TARGETS)
        release = {
            "components": {
                "distribution": {
                    "digest_reference": "ghcr.io/unstaticlabs/usl-odoo@sha256:" + "b" * 64,
                },
            },
        }
        runner = mock.Mock()
        with self.assertRaisesRegex(RuntimeError, "approved runtime file"):
            _reconcile_staging_pocketid(
                target,
                runner,
                release,
                "candidate-network",
                {"odoo_filestore": "candidate-filestore"},
                {"environment_file": "/tmp/untrusted.env"},
            )

        evidence = {
            "schema": "usl-pocket-id-runtime-admission/v1",
            "status": "passed",
            "application_completed": True,
            "provider_enabled": False,
            "governed_provider": True,
            "client_id_matches": True,
            "database_secret_absent": True,
            "issuer_matches": True,
            "base_url_matches": True,
            "required_group_matches": True,
            "scopes_match": True,
            "endpoints_match_issuer": True,
        }
        runner.run.return_value = subprocess.CompletedProcess(
            [], 0, "USL_POCKET_ID_RUNTIME_ADMISSION=" + json.dumps(evidence) + "\n", "",
        )
        with self.assertRaisesRegex(RuntimeError, "evidence differs"):
            _reconcile_staging_pocketid(
                target,
                runner,
                release,
                "candidate-network",
                {"odoo_filestore": "candidate-filestore"},
                {
                    "environment_file": target.value["compose"]["adoption"]["candidate"]["environment_file"],
                },
            )

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
                return subprocess.CompletedProcess(command, 0, "Filesystem Avail\n/dev/root 1073741824\n", "")

        with self.assertRaisesRegex(RuntimeError, "below the 2 GiB safety floor"):
            _require_restore_capacity(target, CapacityRunner(), "preflight")

    def test_restore_capacity_uses_measured_candidate_and_reserve(self) -> None:
        target = load_target("staging", TARGETS)

        class CapacityRunner:
            def __init__(self, available):
                self.available = available

            def run(self, command, *, check=True):
                return subprocess.CompletedProcess(
                    command, 0, f"Filesystem Avail\n/dev/root {self.available}\n", "",
                )

        gib = 1024**3
        with self.assertRaisesRegex(RuntimeError, "2.0 GiB deficit"):
            _require_restore_capacity(
                target,
                CapacityRunner(17 * gib),
                "preflight",
                candidate_bytes={"bulk": 4 * gib},
            )
        admitted = _require_restore_capacity(
            target,
            CapacityRunner(19 * gib),
            "preflight",
            candidate_bytes={"bulk": 4 * gib},
        )
        self.assertEqual(admitted["filesystems"]["/dev/root"]["required_bytes"], 19 * gib)

    def test_restore_capacity_groups_tiers_on_their_actual_filesystems(self) -> None:
        target = load_target("staging", TARGETS)
        gib = 1024**3

        class SplitRunner:
            def run(self, command, *, check=True):
                path = command[-1]
                source = "/dev/volume" if path == "/srv/storage" else "/dev/root"
                available = 30 * gib if source == "/dev/volume" else 10 * gib
                return subprocess.CompletedProcess(
                    command, 0, f"Filesystem Avail\n{source} {available}\n", "",
                )

        result = _require_restore_capacity(
            target,
            SplitRunner(),
            "preflight",
            candidate_bytes={"bulk": 3 * gib, "database": 2 * gib, "local": 1 * gib},
        )
        self.assertEqual(result["filesystems"]["/dev/volume"]["required_bytes"], 18 * gib)
        self.assertEqual(result["filesystems"]["/dev/root"]["candidate_bytes"], 3 * gib)
        self.assertEqual(result["filesystems"]["/dev/root"]["reserve_bytes"], 2 * gib)

    def test_candidate_measurement_counts_unique_volumes_and_required_paths(self) -> None:
        target = load_target("staging", TARGETS)
        runtime = {
            "volumes": {
                "one": {"name": "volume-a", "tier": "bulk"},
                "duplicate": {"name": "volume-a", "tier": "bulk"},
                "two": {"name": "volume-b", "tier": "database"},
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
            {"bulk": 1024, "database": 1024, "local": 1024},
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
                "--",
                target.value["state_directory"] + "/generations/g20260901-a1b2c3d4/work",
            ]],
        )

    def test_materialization_failure_removes_exact_workspace_and_database_containers(self) -> None:
        target = load_target("staging", TARGETS)

        class RecordingRunner:
            def __init__(self):
                self.commands = []

            def run(self, command, *, check=True):
                self.commands.append(command)
                return subprocess.CompletedProcess(command, 0, "", "")

        runner = RecordingRunner()
        generation = "g20260901-a1b2c3d4"
        with self.assertRaisesRegex(RuntimeError, "materialization failed"):
            with _materialization_cleanup(target, runner, generation) as containers:
                containers.append("candidate-postgres")
                raise RuntimeError("materialization failed")
        self.assertEqual(
            runner.commands,
            [
                ["docker", "rm", "--force", "candidate-postgres"],
                [
                    "rm", "-rf", "--",
                    target.value["state_directory"] + f"/generations/{generation}/work",
                ],
            ],
        )

    def test_cleanup_accepts_modern_bind_and_safe_legacy_named_database_volumes(self) -> None:
        target = load_target("staging", TARGETS)
        generation = "g20260904-a1b2c3d4"
        names = generation_volume_names(target, generation)

        class InspectRunner:
            def __init__(self, volume):
                self.volume = volume
                self.commands = []

            def run(self, command, *, check=True):
                self.commands.append(command)
                if command[:3] == ["docker", "volume", "inspect"]:
                    return subprocess.CompletedProcess(command, 0, json.dumps(self.volume), "")
                if command[0] == "find":
                    return subprocess.CompletedProcess(command, 0, "d\n", "")
                raise AssertionError(command)

        labels = {
            "com.unstaticlabs.runtime.project": target.project,
            "com.unstaticlabs.runtime.target": target.name,
            "com.unstaticlabs.runtime.generation": generation,
            "com.unstaticlabs.runtime.role": "odoo_postgres",
            "com.unstaticlabs.runtime.storage-tier": "database",
        }
        database_path = generation_volume_path(target, generation, "odoo_postgres")
        modern = {
            "Name": names["odoo_postgres"],
            "Driver": "local",
            "Labels": labels,
            "Options": {"device": database_path, "o": "bind", "type": "none"},
            "Mountpoint": "/var/lib/docker/volumes/ignored-for-bind/_data",
        }
        self.assertEqual(
            _validated_cleanup_volume(
                target, InspectRunner(modern), modern["Name"], "/var/lib/docker",
            )["database_path"],
            database_path,
        )

        for options in (None, {}):
            legacy = {
                **modern,
                "Labels": {
                    key: value for key, value in labels.items()
                    if not key.endswith("storage-tier")
                },
                "Options": options,
                "Mountpoint": f"/var/lib/docker/volumes/{modern['Name']}/_data",
            }
            legacy_runner = InspectRunner(legacy)
            self.assertIsNone(
                _validated_cleanup_volume(
                    target, legacy_runner, legacy["Name"], "/var/lib/docker",
                )["database_path"],
            )
            self.assertFalse(any(command[0] == "find" for command in legacy_runner.commands))

    def test_cleanup_rejects_foreign_mismatched_and_unsafe_named_volumes(self) -> None:
        target = load_target("staging", TARGETS)
        generation = "g20260904-a1b2c3d4"
        name = generation_volume_names(target, generation)["odoo_postgres"]
        base = {
            "Name": name,
            "Driver": "local",
            "Labels": {
                "com.unstaticlabs.runtime.project": target.project,
                "com.unstaticlabs.runtime.target": target.name,
                "com.unstaticlabs.runtime.generation": generation,
                "com.unstaticlabs.runtime.role": "odoo_postgres",
            },
            "Options": {},
            "Mountpoint": f"/var/lib/docker/volumes/{name}/_data",
        }

        class InspectRunner:
            def __init__(self, volume):
                self.volume = volume

            def run(self, command, *, check=True):
                return subprocess.CompletedProcess(command, 0, json.dumps(self.volume), "")

        mutations = (
            {"Name": "foreign"},
            {"Driver": "nfs"},
            {"Labels": {**base["Labels"], "com.unstaticlabs.runtime.project": "foreign"}},
            {"Labels": {**base["Labels"], "com.unstaticlabs.runtime.target": "production"}},
            {"Labels": {**base["Labels"], "com.unstaticlabs.runtime.generation": "invalid"}},
            {"Labels": {**base["Labels"], "com.unstaticlabs.runtime.role": "foreign"}},
            {"Labels": {**base["Labels"], "com.unstaticlabs.runtime.storage-tier": "bulk"}},
            {"Options": {"device": "/foreign", "o": "bind", "type": "none"}},
            {"Mountpoint": "/foreign"},
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                volume = {**base, **mutation}
                with self.assertRaises(RuntimeError):
                    _validated_cleanup_volume(
                        target, InspectRunner(volume), name, "/var/lib/docker",
                    )

    def test_cleanup_deletes_only_modern_bind_paths(self) -> None:
        class RecordingRunner:
            def __init__(self):
                self.commands = []

            def run(self, command, *, check=True):
                self.commands.append(command)
                return subprocess.CompletedProcess(command, 0, "", "")

        runner = RecordingRunner()
        _delete_cleanup_resources(
            runner,
            [
                {"name": "legacy-named", "database_path": None},
                {"name": "modern-bind", "database_path": "/srv/db/exact"},
            ],
            [],
            [],
        )
        self.assertEqual(
            runner.commands,
            [
                ["docker", "volume", "rm", "legacy-named"],
                ["docker", "volume", "rm", "modern-bind"],
                ["find", "/srv/db/exact", "-xdev", "-mindepth", "1", "-delete"],
                ["rmdir", "--", "/srv/db/exact"],
            ],
        )

    def test_cleanup_rejects_bind_options_for_non_database_volumes(self) -> None:
        target = load_target("staging", TARGETS)
        generation = "g20260904-a1b2c3d4"
        name = generation_volume_names(target, generation)["odoo_filestore"]
        volume = {
            "Name": name,
            "Driver": "local",
            "Labels": {
                "com.unstaticlabs.runtime.project": target.project,
                "com.unstaticlabs.runtime.target": target.name,
                "com.unstaticlabs.runtime.generation": generation,
                "com.unstaticlabs.runtime.role": "odoo_filestore",
                "com.unstaticlabs.runtime.storage-tier": "bulk",
            },
            "Options": {"device": "/foreign", "o": "bind", "type": "none"},
            "Mountpoint": f"/var/lib/docker/volumes/{name}/_data",
        }

        class InspectRunner:
            def run(self, command, *, check=True):
                return subprocess.CompletedProcess(command, 0, json.dumps(volume), "")

        with self.assertRaisesRegex(RuntimeError, "managed volume differs"):
            _validated_cleanup_volume(target, InspectRunner(), name, "/var/lib/docker")

    def test_cleanup_rejects_malformed_network_labels(self) -> None:
        target = load_target("staging", TARGETS)

        class InspectRunner:
            def run(self, command, *, check=True):
                return subprocess.CompletedProcess(
                    command, 0, json.dumps({"Name": "candidate", "Labels": None}), "",
                )

        with self.assertRaisesRegex(RuntimeError, "network inspection is invalid"):
            _validated_cleanup_network(target, InspectRunner(), "candidate")

    def test_cleanup_inventory_protects_active_and_rollback_resources(self) -> None:
        target = load_target("staging", TARGETS)
        active = "active-volume"
        previous = "previous-volume"

        class InventoryRunner:
            def run(self, command, *, check=True):
                if command[0] == "cat":
                    state = {
                        "network": "active-network",
                        "previous": {
                            "generation": "g20260903-rollback",
                            "volumes": {"odoo_postgres": previous},
                            "network": "rollback-network",
                        },
                    }
                    return subprocess.CompletedProcess(command, 0, json.dumps(state), "")
                if command[:3] == ["docker", "volume", "ls"]:
                    return subprocess.CompletedProcess(
                        command, 0, f"{active}\n{previous}\nstale-volume\n", "",
                    )
                if command[:3] == ["docker", "network", "ls"]:
                    return subprocess.CompletedProcess(
                        command, 0,
                        "active-network\nrollback-network\nstale-network\n", "",
                    )
                raise AssertionError(command)

        current = {
            "generation": "g20260904-active",
            "volumes": {"odoo_postgres": {"name": active}},
        }
        with mock.patch("operations.stack._cleanup_workspaces", return_value=["stale-work"]):
            inventory = _cleanup_inventory(target, InventoryRunner(), current)
        self.assertEqual(inventory["delete_volumes"], ["stale-volume"])
        self.assertEqual(inventory["delete_networks"], ["stale-network"])
        self.assertEqual(inventory["delete_workspaces"], ["stale-work"])
        self.assertEqual(
            inventory["protected_generations"],
            ["g20260903-rollback", "g20260904-active"],
        )

    def test_cleanup_workspace_inventory_rejects_symlinks_and_keeps_runs(self) -> None:
        target = load_target("staging", TARGETS)

        class WorkspaceRunner:
            def __init__(self, generation_kind="d"):
                self.generation_kind = generation_kind

            def run(self, command, *, check=True):
                if command[:2] == ["test", "-L"]:
                    return subprocess.CompletedProcess(command, 1, "", "")
                if command[2:6] == ["-mindepth", "0", "-maxdepth", "0"]:
                    return subprocess.CompletedProcess(command, 0, "d\n", "")
                if command[2:6] == ["-mindepth", "1", "-maxdepth", "1"]:
                    return subprocess.CompletedProcess(
                        command, 0,
                        f"g20260904-active\td\ng20260903-stale\t{self.generation_kind}\n", "",
                    )
                if command[2:6] == ["-mindepth", "2", "-maxdepth", "2"]:
                    return subprocess.CompletedProcess(
                        command, 0,
                        "g20260904-active/work\td\ng20260903-stale/work\td\n", "",
                    )
                raise AssertionError(command)

        workspaces = _cleanup_workspaces(
            target, WorkspaceRunner(), {"g20260904-active"},
        )
        self.assertEqual(
            workspaces,
            [target.value["state_directory"] + "/generations/g20260903-stale/work"],
        )
        with self.assertRaises(RuntimeError):
            _cleanup_workspaces(target, WorkspaceRunner("l"), set())

    def test_cleanup_apply_recomputes_after_lock_and_prevalidates_everything(self) -> None:
        configured_target = load_target("staging", TARGETS)
        generation = "g20260903-stale"
        names = generation_volume_names(configured_target, generation)
        candidates = [names["odoo_postgres"], names["paperless_postgres"]]
        labels = {
            "com.unstaticlabs.runtime.project": configured_target.project,
            "com.unstaticlabs.runtime.target": configured_target.name,
            "com.unstaticlabs.runtime.generation": generation,
        }
        volumes = {
            candidates[0]: {
                "Name": candidates[0], "Driver": "local", "Options": None,
                "Mountpoint": f"/var/lib/docker/volumes/{candidates[0]}/_data",
                "Labels": {**labels, "com.unstaticlabs.runtime.role": "odoo_postgres"},
            },
            candidates[1]: {
                "Name": candidates[1], "Driver": "local", "Options": None,
                "Mountpoint": f"/var/lib/docker/volumes/{candidates[1]}/_data",
                "Labels": {
                    **labels, "com.unstaticlabs.runtime.target": "production",
                    "com.unstaticlabs.runtime.role": "paperless_postgres",
                },
            },
        }

        class CleanupRunner:
            def __init__(self):
                self.commands = []

            def run(self, command, *, check=True):
                self.commands.append(command)
                if command[:2] == ["docker", "info"]:
                    return subprocess.CompletedProcess(command, 0, "/var/lib/docker\n", "")
                if command[:3] == ["docker", "volume", "inspect"]:
                    return subprocess.CompletedProcess(
                        command, 0, json.dumps(volumes[command[3]]), "",
                    )
                raise AssertionError(command)

        runner = CleanupRunner()
        target = mock.Mock(
            name=configured_target.name,
            project=configured_target.project,
            value=configured_target.value,
        )
        target.name = configured_target.name
        target.project = configured_target.project
        target.value = configured_target.value
        target.runner.return_value = runner
        locked = {"value": False}

        class Lock:
            def __enter__(self):
                locked["value"] = True

            def __exit__(self, *_args):
                locked["value"] = False

        current = {"generation": "g20260904-active", "volumes": {}}

        def inspect_after_lock(_target, _runner):
            self.assertTrue(locked["value"])
            return current

        def inventory_after_lock(_target, _runner, value):
            self.assertTrue(locked["value"])
            self.assertIs(value, current)
            return {
                "protected_volumes": [], "protected_networks": [],
                "protected_generations": ["g20260904-active"],
                "delete_volumes": candidates,
                "delete_networks": [], "delete_workspaces": [],
            }

        with (
            mock.patch("operations.stack.load_target", return_value=target),
            mock.patch("operations.stack.runtime_lock", return_value=Lock()),
            mock.patch("operations.stack.inspect_runtime", side_effect=inspect_after_lock),
            mock.patch("operations.stack._cleanup_inventory", side_effect=inventory_after_lock),
            self.assertRaisesRegex(RuntimeError, "candidate identity differs"),
        ):
            cleanup_command(argparse.Namespace(
                target="staging", targets=TARGETS, action="apply", confirm="staging",
                runtime_only=True, json=True,
            ))
        self.assertEqual(
            [command[3] for command in runner.commands if command[:3] == ["docker", "volume", "inspect"]],
            candidates,
        )
        self.assertFalse(any(
            command[:3] in (["docker", "volume", "rm"], ["docker", "network", "rm"])
            or command[:3] == ["rm", "-rf", "--"]
            for command in runner.commands
        ))

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
        target = mock.Mock(value={"compose": {}})
        identity = {
            "project": "safe-project",
            "working_directory": "/release",
            "environment_file": "/runtime.env",
            "compose_files": ["/release/compose.yaml"],
        }

        class FailedRunner:
            def run(self, command, *, check=True):
                if check:
                    raise RuntimeError("disk full")
                return subprocess.CompletedProcess(command, 1, "", "disk full")

        with self.assertRaisesRegex(RuntimeError, "activation failed \\(original failure\\).*disk full"):
            _rollback_after_failure(target, FailedRunner(), identity, RuntimeError("original failure"))

    def test_rollback_reactivates_the_previous_generation_overlay(self) -> None:
        target = mock.Mock(value={"compose": {}})
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
        _rollback_after_failure(target, runner, identity, RuntimeError("new generation failed"))
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

    def test_generation_database_volumes_bind_to_exact_nvme_paths(self) -> None:
        target = load_target("staging", TARGETS)

        class RecordingRunner:
            def __init__(self):
                self.commands = []

            def run(self, command, *, check=True):
                self.commands.append(command)
                status = 1 if command[:3] in (["docker", "volume", "inspect"], ["docker", "network", "inspect"]) else 0
                return subprocess.CompletedProcess(command, status, "", "")

        runner = RecordingRunner()
        generation = "g20260903-storage"
        _create_generation_resources(target, runner, generation)
        path = generation_volume_path(target, generation, "odoo_postgres")
        creates = [command for command in runner.commands if command[:3] == ["docker", "volume", "create"]]
        database = next(command for command in creates if command[-1].endswith("odoo-postgres"))
        filestore = next(command for command in creates if command[-1].endswith("odoo-filestore"))
        self.assertIn(f"device={path}", database)
        self.assertIn("com.unstaticlabs.runtime.storage-tier=database", " ".join(database))
        self.assertNotIn("type=none", filestore)

    def test_generation_database_path_rejects_traversal(self) -> None:
        target = load_target("staging", TARGETS)
        with self.assertRaisesRegex(RuntimeError, "generation name is invalid"):
            generation_volume_path(target, "g../../root", "odoo_postgres")

    def test_active_candidate_and_rollback_database_paths_are_distinct(self) -> None:
        target = load_target("production", TARGETS)
        paths = {
            generation_volume_path(target, generation, "odoo_postgres")
            for generation in ("gactive", "gcandidate", "grollback")
        }
        self.assertEqual(len(paths), 3)
        self.assertTrue(all(path.startswith("/srv/db/usl-odoo/production/generations/") for path in paths))

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

    def test_host_staging_overlays_use_the_canonical_odoo_service(self) -> None:
        target = load_target("staging", HOST_TARGETS)
        resources = json.loads(_resource_overlay(target))
        self.assertIn("odoo-staging", resources["services"])
        self.assertNotIn("odoo", resources["services"])

        digest = "ghcr.io/unstaticlabs/usl-odoo@sha256:" + "a" * 64
        release = {
            "components": {
                "distribution": {"digest_reference": digest},
                "paperless": {"digest_reference": digest},
                "sign-dss": {"digest_reference": digest},
            },
            "mcp": {"image": digest},
            "renderer": {"image": digest},
        }
        overlay = json.loads(
            _generation_overlay(
                generation_volume_names(target, "g20260904-canonical"),
                release,
                {"odoo", "odoo-staging"},
                target.value["ingress"],
                service_names=target.value["services"],
            ),
        )
        self.assertIn("odoo-staging", overlay["services"])
        self.assertNotIn("odoo", overlay["services"])
        self.assertEqual(
            overlay["services"]["odoo-staging"]["environment"]["ODOO_DB_FILTER"],
            "^odoo_staging$",
        )

    def test_production_resource_overlay_service_is_unchanged(self) -> None:
        target = load_target("production", HOST_TARGETS)
        resources = json.loads(_resource_overlay(target))
        self.assertIn("odoo", resources["services"])
        self.assertNotIn("odoo-staging", resources["services"])

    def test_first_staging_activation_stops_and_rolls_back_the_legacy_identity(self) -> None:
        target = load_target("staging", HOST_TARGETS)
        legacy = {
            "project": target.project,
            "working_directory": "/gitops/staging",
            "environment_file": "/runtime/staging.env",
            "profiles": [],
            "anchor_service": "odoo",
            "compose_files": ["/gitops/staging/compose.yaml", "/runtime/v2.json"],
        }
        candidate = {
            **legacy,
            "anchor_service": "odoo-staging",
            "compose_files": ["/gitops/staging/compose.yaml", "/runtime/v3.json"],
        }

        class FailingCandidateRunner:
            def __init__(self):
                self.commands = []

            def run(self, command, *, check=True):
                self.commands.append(command)
                if "/runtime/v3.json" in command:
                    raise RuntimeError("candidate failed")
                if command[:2] == ["docker", "ps"]:
                    return subprocess.CompletedProcess(command, 0, "candidate-id\n", "")
                if command[:3] == ["docker", "inspect", "candidate-id"]:
                    return subprocess.CompletedProcess(
                        command,
                        0,
                        json.dumps({
                            "com.docker.compose.project": target.project,
                            "com.docker.compose.service": "odoo-staging",
                        }),
                        "",
                    )
                return subprocess.CompletedProcess(command, 0, "", "")

        runner = FailingCandidateRunner()
        with mock.patch("operations.stack._start_rollback_identity") as starter, self.assertRaisesRegex(
            RuntimeError, "candidate failed",
        ):
            _activate_generation(target, runner, legacy, candidate)

        stop = runner.commands[0]
        failed_up = runner.commands[1]
        self.assertEqual(stop[-14:-10], ["stop", "--timeout", "60", "db"])
        self.assertIn("odoo", stop)
        self.assertNotIn("odoo-staging", stop)
        self.assertIn("/runtime/v3.json", failed_up)
        starter.assert_called_once_with(target, runner, legacy)
        self.assertIn(["docker", "rm", "--force", "candidate-id"], runner.commands)

    def test_first_staging_candidate_uses_only_the_allowlisted_gitops_identity(self) -> None:
        target = load_target("staging", HOST_TARGETS)
        legacy = {
            "project": target.project,
            "working_directory": "/var/lib/usl-odoo/runtime/staging/validation-v2",
            "environment_file": "/var/lib/usl-odoo/runtime/staging/validation-v2/site.env",
            "profiles": [],
            "anchor_service": "odoo",
            "compose_files": [
                "/var/lib/usl-odoo/runtime/staging/validation-v2/compose.yaml",
            ],
        }

        class CandidateRunner:
            def __init__(self):
                self.commands = []

            def run(self, command, *, check=True):
                self.commands.append(command)
                if command[:2] in (["test", "-f"], ["test", "-d"]):
                    return subprocess.CompletedProcess(command, 0, "", "")
                if command[:2] == ["readlink", "-f"]:
                    return subprocess.CompletedProcess(command, 0, command[-1] + "\n", "")
                if command[-2:] == ["config", "--services"]:
                    services = "".join(
                        f"{service}\n"
                        for service in sorted(set(target.value["services"].values()))
                    )
                    return subprocess.CompletedProcess(command, 0, services, "")
                raise AssertionError(command)

        runner = CandidateRunner()
        candidate = _candidate_compose_identity(target, runner, legacy)
        contract = target.value["compose"]["adoption"]["candidate"]
        self.assertEqual(candidate["working_directory"], contract["working_directory"])
        self.assertEqual(candidate["compose_files"], contract["compose_files"])
        self.assertEqual(candidate["environment_file"], contract["environment_file"])
        self.assertNotIn(legacy["compose_files"][0], candidate["compose_files"])
        config_command = runner.commands[-1]
        self.assertIn(contract["compose_files"][0], config_command)
        self.assertNotIn(legacy["compose_files"][0], config_command)

    def test_second_staging_activation_uses_only_the_canonical_service(self) -> None:
        target = load_target("staging", HOST_TARGETS)
        current = {
            "project": target.project,
            "working_directory": "/gitops/staging",
            "environment_file": "/runtime/staging.env",
            "profiles": [],
            "anchor_service": "odoo-staging",
            "compose_files": ["/gitops/staging/compose.yaml", "/runtime/v3-active.json"],
        }
        candidate = {**current, "compose_files": ["/gitops/staging/compose.yaml", "/runtime/v3-next.json"]}
        runner = mock.Mock()
        runner.run.return_value = subprocess.CompletedProcess([], 0, "", "")

        _activate_generation(target, runner, current, candidate)

        stop = runner.run.call_args_list[0].args[0]
        self.assertIn("odoo-staging", stop)
        self.assertNotIn("odoo", stop)
        self.assertIn("/runtime/v3-next.json", runner.run.call_args_list[1].args[0])

    def test_first_v3_gateway_adoption_rolls_back_when_gateway_start_fails(self) -> None:
        target = load_target("staging", HOST_TARGETS)
        legacy = {
            "container_id": "legacy-id",
            "project": target.project,
            "working_directory": "/runtime/legacy",
            "environment_file": "/runtime/staging.env",
            "profiles": target.value["compose"]["profiles"],
            "anchor_service": "odoo",
            "compose_files": ["/runtime/legacy/compose.yaml"],
        }
        candidate = {**legacy, "anchor_service": "odoo-staging"}

        class Runner:
            detached = False
            commands = []

            def run(self, command, *, check=True, input_text=None):
                self.commands.append(command)
                if command[:2] == ["test", "-f"]:
                    return subprocess.CompletedProcess(command, 0, "", "")
                if command[:3] == ["docker", "network", "disconnect"]:
                    self.detached = True
                    return subprocess.CompletedProcess(command, 0, "", "")
                if command[:3] == ["docker", "network", "connect"]:
                    self.detached = False
                    return subprocess.CompletedProcess(command, 0, "", "")
                if "up" in command and command[-1] == "gateway":
                    raise RuntimeError("gateway start failed")
                raise AssertionError(command)

        runner = Runner()
        networks = lambda _runner, container: {
            target.value["compose"]["default_network"]: {"Aliases": ["odoo-staging-app"]},
            **({target.value["external_networks"]["ingress"]: {"Aliases": [
                f"{target.project}-odoo-1", "odoo", "odoo-staging",
            ]}} if not runner.detached else {}),
        }
        owners = lambda _runner, _network, _alias: [] if runner.detached else ["legacy-id"]
        with mock.patch("operations.stack.inspect_runtime", return_value={"compose": legacy, "generation": "glegacy"}), mock.patch(
            "operations.stack._candidate_compose_identity", return_value=candidate,
        ), mock.patch(
            "operations.stack._validated_legacy_compose_identity",
        ), mock.patch(
            "operations.stack._container_identifier", return_value="legacy-id",
        ), mock.patch("operations.stack._container_networks", side_effect=networks), mock.patch(
            "operations.stack._network_alias_owners", side_effect=owners,
        ), mock.patch("operations.stack._gateway_container", return_value=None), self.assertRaisesRegex(
            RuntimeError, "gateway start failed",
        ):
            _adopt_staging_gateway(target, runner)
        self.assertFalse(runner.detached)

    def test_first_v3_gateway_adoption_is_retryable_after_alias_transfer(self) -> None:
        target = load_target("staging", HOST_TARGETS)
        ingress = target.value["external_networks"]["ingress"]
        default = target.value["compose"]["default_network"]
        legacy = {
            "container_id": "legacy-id", "project": target.project,
            "working_directory": "/runtime/legacy",
            "environment_file": "/runtime/staging.env",
            "profiles": target.value["compose"]["profiles"],
            "anchor_service": "odoo", "compose_files": ["/runtime/legacy.yaml"],
        }
        candidate = {**legacy, "anchor_service": "odoo-staging"}

        class Runner:
            detached = True
            gateway = False
            commands = []

            def run(self, command, *, check=True, input_text=None):
                self.commands.append(command)
                if command[:2] == ["test", "-f"] or command[:3] == ["docker", "exec", "gateway-id"]:
                    return subprocess.CompletedProcess(command, 0, "", "")
                if "up" in command and command[-1] == "gateway":
                    self.gateway = True
                    return subprocess.CompletedProcess(command, 0, "", "")
                raise AssertionError(command)

        runner = Runner()
        def networks(_runner, container):
            if container == "legacy-id":
                return {default: {"Aliases": ["odoo-staging-app"]}}
            return {ingress: {"Aliases": ["odoo-staging"]}, default: {"Aliases": ["gateway"]}}

        def owners(_runner, _network, _alias):
            return ["gateway-id"] if runner.gateway else []

        with mock.patch("operations.stack.inspect_runtime", return_value={"compose": legacy, "generation": "glegacy"}), mock.patch(
            "operations.stack._candidate_compose_identity", return_value=candidate,
        ), mock.patch(
            "operations.stack._validated_legacy_compose_identity",
        ), mock.patch(
            "operations.stack._container_identifier", return_value="legacy-id",
        ), mock.patch("operations.stack._container_networks", side_effect=networks), mock.patch(
            "operations.stack._network_alias_owners", side_effect=owners,
        ), mock.patch("operations.stack._gateway_container", side_effect=lambda *_: "gateway-id" if runner.gateway else None), mock.patch(
            "operations.stack._validate_gateway_container",
        ), mock.patch(
            "operations.stack._probe_staging_gateway_maintenance",
            return_value={"schema": "usl-staging-gateway-maintenance/v1", "status": "passed", "http_status": 503, "websocket_status": 503},
        ):
            first = _adopt_staging_gateway(target, runner)
            second = _adopt_staging_gateway(target, runner)
        self.assertEqual(
            set(first),
            {"schema", "status", "adoption", "http_status", "websocket_status"},
        )
        self.assertEqual(first["adoption"], "already-adopted")
        self.assertEqual(second["adoption"], "already-adopted")

    def test_first_v3_gateway_adoption_stops_on_alias_transfer_failure(self) -> None:
        target = load_target("staging", HOST_TARGETS)
        ingress = target.value["external_networks"]["ingress"]
        default = target.value["compose"]["default_network"]
        legacy = {
            "container_id": "legacy-id", "project": target.project,
            "working_directory": "/runtime/legacy", "environment_file": "/runtime/staging.env",
            "profiles": target.value["compose"]["profiles"], "anchor_service": "odoo",
            "compose_files": ["/runtime/legacy.yaml"],
        }

        class Runner:
            commands = []

            def run(self, command, *, check=True, input_text=None):
                self.commands.append(command)
                if command[:2] == ["test", "-f"]:
                    return subprocess.CompletedProcess(command, 0, "", "")
                if command[:3] == ["docker", "network", "disconnect"]:
                    raise RuntimeError("alias transfer failed")
                raise AssertionError(command)

        runner = Runner()
        with mock.patch("operations.stack.inspect_runtime", return_value={"compose": legacy, "generation": "glegacy"}), mock.patch(
            "operations.stack._candidate_compose_identity", return_value={**legacy, "anchor_service": "odoo-staging"},
        ), mock.patch(
            "operations.stack._validated_legacy_compose_identity",
        ), mock.patch(
            "operations.stack._container_identifier", return_value="legacy-id",
        ), mock.patch("operations.stack._container_networks", return_value={
            default: {"Aliases": ["odoo-staging-app"]},
            ingress: {"Aliases": [f"{target.project}-odoo-1", "odoo", "odoo-staging"]},
        }), mock.patch(
            "operations.stack._network_alias_owners", return_value=["legacy-id"],
        ), self.assertRaisesRegex(RuntimeError, "alias transfer failed"):
            _adopt_staging_gateway(target, runner)
        self.assertFalse(any("gateway" == command[-1] for command in runner.commands))

    def test_gateway_maintenance_probe_requires_http_and_websocket_503(self) -> None:
        target = load_target("staging", HOST_TARGETS)

        class Runner:
            responses = [
                subprocess.CompletedProcess([], 0, "HTTP/1.1 503 Service Unavailable\nRetry-After: 60\n", ""),
                subprocess.CompletedProcess([], 0, 'HTTP/1.1 503 Service Unavailable\n\n{"error":"maintenance"}\n', ""),
            ]

            def run(self, command, *, check=True, input_text=None):
                return self.responses.pop(0)

        evidence = _probe_staging_gateway_maintenance(target, Runner())
        self.assertEqual(
            set(evidence),
            {"schema", "status", "http_status", "websocket_status"},
        )
        self.assertEqual(evidence["status"], "passed")
        self.assertEqual(evidence["http_status"], 503)
        self.assertEqual(evidence["websocket_status"], 503)

    def test_legacy_rollback_starts_behind_the_stable_gateway(self) -> None:
        target = load_target("staging", HOST_TARGETS)
        legacy = {
            "project": target.project, "working_directory": "/runtime/legacy",
            "environment_file": "/runtime/staging.env", "profiles": [],
            "anchor_service": "odoo", "compose_files": ["/runtime/legacy.yaml"],
        }

        class Runner:
            commands = []

            def run(self, command, *, check=True, input_text=None):
                self.commands.append(command)
                if command[:2] == ["docker", "ps"]:
                    return subprocess.CompletedProcess(command, 0, "legacy-id\n", "")
                return subprocess.CompletedProcess(command, 0, "", "")

        runner = Runner()
        with mock.patch("operations.stack._gateway_container", return_value="gateway-id"), mock.patch(
            "operations.stack._candidate_compose_identity", return_value={**legacy, "anchor_service": "odoo-staging"},
        ), mock.patch(
            "operations.stack._validate_gateway_container",
        ), mock.patch(
            "operations.stack._network_alias_owners", return_value=["gateway-id"],
        ), mock.patch(
            "operations.stack._container_networks", return_value={
                target.value["compose"]["default_network"]: {"Aliases": ["odoo-staging-app"]},
                target.value["external_networks"]["ingress"]: {"Aliases": ["odoo-staging"]},
            },
        ), mock.patch("operations.stack._wait_compose_services"):
            _start_rollback_identity(target, runner, legacy)
        create = next(command for command in runner.commands if "create" in command)
        start = next(command for command in runner.commands if "start" in command)
        self.assertIn("--force-recreate", create)
        self.assertIn(["docker", "network", "disconnect", "cloudflare", "legacy-id"], runner.commands)
        self.assertNotIn("gateway", create[create.index("create") + 1:])
        self.assertIn("odoo", start)
        self.assertFalse(any("up" in command for command in runner.commands))

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
