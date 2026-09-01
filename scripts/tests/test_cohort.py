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
from operations.stack import _cohort_command, with_writers_paused


ROOT = Path(__file__).resolve().parents[2]
TARGETS = ROOT / "operations/targets"


def manifest(durable: dict, cache: dict) -> dict:
    return {
        "schema": cohort.SCHEMA,
        "run_id": "20260901t120000z-a1b2c3d4",
        "created_at": "2026-09-01T12:00:00Z",
        "target": "production",
        "release": {"commit": "a" * 40, "manifest_sha256": "b" * 64},
        "ollama": {"model": "bge-m3:latest", "manifest_sha256": "c" * 64, "dimension": 1024},
        "databases": {
            "odoo": {"name": "odoo_production", "bytes": 1, "sha256": "d" * 64},
            "paperless": {"name": "paperless", "bytes": 1, "sha256": "e" * 64},
        },
        "durable": durable,
        "cache": cache,
        "cache_snapshot_id": None,
    }


class CohortContractTests(unittest.TestCase):
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
            self.assertEqual(result["cache_snapshot_id"], "1" * 64)
            self.assertEqual(result["durable_snapshot_id"], "2" * 64)

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


if __name__ == "__main__":
    unittest.main()
