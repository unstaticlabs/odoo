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
    VOLUME_LOGICAL_NAMES,
    _cohort_command,
    _generation_overlay,
    _materialize_command,
    _restore_unlocked,
    _validate_materialized_release,
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
            }
        },
        "cache_snapshot_id": None,
    }


class CohortContractTests(unittest.TestCase):
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
        release = {"source": {"commit": "a" * 40}}
        materialized = {
            "release": {"commit": "a" * 40, "manifest_sha256": "b" * 64},
        }
        _validate_materialized_release(materialized, release, "b" * 64)
        materialized["release"]["manifest_sha256"] = "c" * 64
        with self.assertRaisesRegex(RuntimeError, "differs from the cohort"):
            _validate_materialized_release(materialized, release, "b" * 64)

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
        ):
            result = cohort.qualify(arguments)
        verify.assert_called_once_with(arguments)
        self.assertEqual(result["status"], "qualified")
        self.assertEqual(len(calls), 2)
        self.assertTrue(all("recovery-eligible" in command for command, _environment in calls))

    def test_staging_restore_isolates_production_mcp_oauth_state(self) -> None:
        self.assertFalse(cohort.should_restore_resource("mcp_oauth", "staging"))
        self.assertFalse(cohort.should_restore_resource("mcp_oauth", "local"))
        self.assertTrue(cohort.should_restore_resource("mcp_oauth", "production"))
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
        self.assertGreaterEqual(joined.count(":ro"), 7)

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
        self.assertIn("up", runner.commands[-1])
        self.assertIn("--no-recreate", runner.commands[-1])

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
        overlay = json.loads(_generation_overlay(names, release, services))
        self.assertEqual(set(overlay["services"]), services)
        self.assertTrue(
            all(item["image"] == reference and item["build"] is None for item in overlay["services"].values()),
        )

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
        self.assertIn("USL_RESTORE_GENERATION_CONFIRMED=g20260901-a1b2c3d4", joined)
        self.assertIn("USL_TARGET_ENVIRONMENT=staging", joined)


if __name__ == "__main__":
    unittest.main()
