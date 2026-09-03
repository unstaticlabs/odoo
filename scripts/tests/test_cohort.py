from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from operations import cohort
from operations.runtime import load_target
from operations.stack import (
    BACKUP_WRITER_SERVICE_ROLES,
    VOLUME_LOGICAL_NAMES,
    _cohort_command,
    _generation_overlay,
    _ensure_image,
    _materialize_command,
    _prepare_generation_volume_ownership,
    _release_images,
    _remove_materialization_workspace,
    _require_restore_capacity,
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
