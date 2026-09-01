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
        if command[:1] == ["cat"]:
            return subprocess.CompletedProcess(command, 1, "", "missing")
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
            target = load_target(name, TARGETS)
            self.assertEqual(target.name, name)
            self.assertEqual(
                set(target.value["compose"]["profiles"]),
                {"document-renderer", "mcp", "paperless", "sign"},
            )

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
        command = compose_command(identity, ["ps"])
        self.assertIn("--env-file", command)
        for profile in target.value["compose"]["profiles"]:
            self.assertIn(profile, command)

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

    def test_status_accepts_only_the_recorded_active_generation(self) -> None:
        target = load_target("staging", TARGETS)
        generation = "g20260901-a1b2c3d4"
        volumes = {
            role: f"generation-{role}"
            for role in target.value["volumes"]
        }
        state = {
            "schema": "usl-active-generation/v1",
            "target": target.name,
            "generation": generation,
            "volumes": volumes,
            "network": "generation-network",
            "snapshot": "a" * 64,
            "release_manifest": (
                target.value["state_directory"]
                + f"/generations/{generation}/usl-release.json"
            ),
            "previous": {},
        }

        class GenerationRunner(FakeRunner):
            def run(self, command: list[str], *, check: bool = True):
                if command[:1] == ["cat"]:
                    return self.completed(command, json.dumps(state))
                if command[:3] == ["docker", "volume", "inspect"]:
                    name = command[3]
                    role = next(key for key, value in volumes.items() if value == name)
                    return self.completed(
                        command,
                        json.dumps(
                            {
                                "com.unstaticlabs.runtime.project": target.project,
                                "com.unstaticlabs.runtime.target": target.name,
                                "com.unstaticlabs.runtime.generation": generation,
                                "com.unstaticlabs.runtime.role": role,
                            },
                        ),
                    )
                return super().run(command, check=check)

        result = inspect_runtime(target, GenerationRunner(target))
        self.assertEqual(result["generation"], generation)
        self.assertEqual(result["active_state"]["snapshot"], "a" * 64)


if __name__ == "__main__":
    unittest.main()
