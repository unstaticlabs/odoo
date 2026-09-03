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
from operations.stack import health_command


ROOT = Path(__file__).resolve().parents[2]
TARGETS = ROOT / "operations/targets"


class HealthRunner:
    def __init__(self, target, *, proxy_mode: bool = True) -> None:
        self.target = target
        self.proxy_mode = proxy_mode
        self.commands: list[list[str]] = []

    def run(self, command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        if command[0] == "curl":
            if "--include" in command:
                return subprocess.CompletedProcess(
                    command,
                    28,
                    "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n",
                    "curl: timeout after upgrade",
                )
            if "--fail" in command:
                return subprocess.CompletedProcess(
                    command,
                    0,
                    json.dumps(
                        {
                            "schema": "usl-odoo-mcp-readiness/v1",
                            "status": "ready",
                            "server_version": "1.0.0",
                            "targets": 1,
                            "oauth": {"status": "ready", "schema_version": 1},
                        }
                    ),
                    "",
                )
            return subprocess.CompletedProcess(command, 0, "200", "")
        joined = " ".join(command)
        if "configparser" in joined:
            value = {
                "proxy_mode": self.proxy_mode,
                "list_db": False,
                "dbfilter": "^odoo_production$",
            }
            return subprocess.CompletedProcess(command, 0, json.dumps(value), "")
        if "USL health" in joined:
            value = {
                "digest": self.target.value["ollama"]["manifest_sha256"],
                "dimension": 1024,
            }
            return subprocess.CompletedProcess(command, 0, json.dumps(value), "")
        if "usl-sign-readiness/v1" in joined:
            value = {
                "schema": "usl-sign-readiness/v1",
                "status": "ready",
                "step_ca": {"status": "ok", "trust_sha256": "a" * 64},
                "dss": {
                    "status": "ok",
                    "engine_version": "6.4",
                    "trust_sha256": "b" * 64,
                },
            }
            return subprocess.CompletedProcess(command, 0, json.dumps(value), "")
        raise AssertionError(command)


class TargetWithRunner:
    def __init__(self, target, runner) -> None:
        self.path = target.path
        self.value = target.value
        self._runner = runner

    @property
    def name(self):
        return self.value["name"]

    def runner(self):
        return self._runner


class HealthContractTests(unittest.TestCase):
    def run_health(self, *, proxy_mode: bool = True) -> tuple[int, dict]:
        target = load_target("production", TARGETS)
        runner = HealthRunner(target, proxy_mode=proxy_mode)
        wrapped = TargetWithRunner(target, runner)
        containers = [
            {"Service": service, "State": "running", "Health": "healthy"}
            for service in target.value["services"].values()
        ]
        status = {
            "containers": containers,
            "compose": {
                "project": target.project,
                "working_directory": "/release",
                "environment_file": "/runtime/target.env",
                "compose_files": ["/release/compose.yaml"],
                "profiles": target.value["compose"]["profiles"],
            },
        }
        output = io.StringIO()
        with (
            patch("operations.stack.load_target", return_value=wrapped),
            patch("operations.stack.inspect_runtime", return_value=status),
            redirect_stdout(output),
        ):
            result = health_command(
                argparse.Namespace(target="production", targets=TARGETS, json=True),
            )
        return result, json.loads(output.getvalue())

    def test_health_proves_proxy_config_https_and_websocket(self) -> None:
        result, report = self.run_health()
        self.assertEqual(result, 0)
        self.assertEqual(report["status"], "passed")
        self.assertTrue(report["odoo_config"]["proxy_mode"])
        self.assertEqual(report["websocket"]["status_code"], 101)

    def test_health_rejects_false_proxy_mode(self) -> None:
        result, report = self.run_health(proxy_mode=False)
        self.assertEqual(result, 2)
        self.assertIn("odoo:config-mismatch", report["failures"])


if __name__ == "__main__":
    unittest.main()
