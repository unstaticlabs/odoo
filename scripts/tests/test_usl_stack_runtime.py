from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

from operations.runtime import (
    RuntimeError,
    compose_command,
    compose_identity,
    inspect_runtime,
    load_target,
    validate_secret_text,
)


ROOT = Path(__file__).resolve().parents[2]
TARGETS = ROOT / "operations/targets"


class FakeRunner:
    def __init__(self, target) -> None:
        self.target = target
        self.commands: list[list[str]] = []

    def completed(self, command: list[str], output: str) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, output, "")

    def run(self, command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        if command[:2] == ["docker", "ps"]:
            return self.completed(command, "anchor-id\n")
        if command[:3] == ["docker", "inspect", "anchor-id"]:
            labels = {
                "com.docker.compose.project": self.target.project,
                "com.docker.compose.project.config_files": "/release/compose.yaml,/release/production.yaml",
                "com.docker.compose.project.working_dir": "/release",
                "com.docker.compose.project.environment_file": "/runtime/target.env",
            }
            return self.completed(command, json.dumps(labels))
        if command[:3] == ["docker", "volume", "inspect"]:
            return self.completed(
                command,
                json.dumps({"com.docker.compose.project": self.target.project}),
            )
        if "compose" in command and "ps" in command:
            return self.completed(
                command,
                json.dumps(
                    {
                        "Name": f"{self.target.project}-odoo-1",
                        "Project": self.target.project,
                        "Service": "odoo",
                        "State": "running",
                        "Health": "healthy",
                    },
                )
                + "\n",
            )
        raise AssertionError(command)


class RuntimeContractTests(unittest.TestCase):
    def test_all_versioned_targets_validate(self) -> None:
        for name in ("production", "staging", "local"):
            self.assertEqual(load_target(name, TARGETS).name, name)

    def test_secret_file_rejects_scope_fields(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "topology key"):
            validate_secret_text(
                "RESTIC_PASSWORD=secret\nCOMPOSE_PROJECT_NAME=foreign\n",
                ["RESTIC_PASSWORD"],
            )

    def test_secret_file_rejects_unapproved_secret(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "not allowlisted"):
            validate_secret_text("UNKNOWN_TOKEN=secret\n", ["RESTIC_PASSWORD"])

    def test_adopts_compose_provenance_from_exact_anchor(self) -> None:
        target = load_target("production", TARGETS)
        runner = FakeRunner(target)
        identity = compose_identity(target, runner)
        self.assertEqual(identity["project"], target.project)
        self.assertEqual(identity["compose_files"], ["/release/compose.yaml", "/release/production.yaml"])
        self.assertIn("--env-file", compose_command(identity, ["ps"]))

    def test_status_rejects_foreign_volume_ownership(self) -> None:
        target = load_target("production", TARGETS)

        class ForeignVolumeRunner(FakeRunner):
            def run(self, command: list[str], *, check: bool = True):
                if command[:3] == ["docker", "volume", "inspect"]:
                    return self.completed(command, json.dumps({"com.docker.compose.project": "foreign"}))
                return super().run(command, check=check)

        with self.assertRaisesRegex(RuntimeError, "not owned"):
            inspect_runtime(target, ForeignVolumeRunner(target))

    def test_status_reports_only_exact_owned_resources(self) -> None:
        target = load_target("staging", TARGETS)
        result = inspect_runtime(target, FakeRunner(target))
        self.assertEqual(result["target"], "staging")
        self.assertEqual(set(result["volumes"]), set(target.value["volumes"]))


if __name__ == "__main__":
    unittest.main()
