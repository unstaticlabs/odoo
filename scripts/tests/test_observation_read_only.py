"""Observation must be unable to write to a host, whatever code it shares.

`scripts/usl-stack-observe` reaches the same health and smoke handlers that a
release runs. Those once provisioned the MCP authority override on the host
(`install -d` and a file write) whenever the launcher's GitOps variables were
set, and stopped with "exact GitOps identity is missing" when they were not.
These tests hold the observer to reads only, and prove it can still verify the
deployed MCP authority and the generation's admission receipt.
"""

from __future__ import annotations

import hashlib
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from operations import runtime
from operations.runtime import (
    ReadOnlyRunner,
    load_target,
    observation_only,
    read_only_command_problem,
)
from operations.stack import (
    _observed_admission_receipt,
    _runtime_admission_evidence,
    _with_mcp_runtime_authority,
    _write_remote,
)

ROOT = Path(__file__).resolve().parents[2]
TARGETS = ROOT / "operations/targets"
GITOPS_COMMIT = "4" * 40
RUN_ROOT = f"/var/lib/usl-odoo/gitops-runs/{GITOPS_COMMIT}.Iabc123"
MCP_IMAGE = "ghcr.io/unstaticlabs/odoo-mcp@sha256:" + "b" * 64
RELEASE_IDENTITY = "1" * 64
GENERATION = "grel-0123456789abcdef0123"


def ledger(environment: str) -> tuple[dict, dict]:
    selected = {
        "schema": "usl-odoo-mcp-environment-release/v1",
        "environment": environment,
        "commit": "a" * 40,
        "compatibility_sha256": "c" * 64,
        "image": MCP_IMAGE,
        "oauth_vault_schema": 1,
        "release_manifest": "ghcr.io/unstaticlabs/usl-odoo-mcp-release@sha256:" + "d" * 64,
    }
    manifest = {
        "schema": "usl-odoo-mcp-oci-release/v2",
        "source": {
            "repository": "https://github.com/unstaticlabs/odoo-mcp.git",
            "ref": "refs/heads/main",
            "commit": selected["commit"],
        },
        "image": {"digest_reference": MCP_IMAGE},
        "compatibility": {"sha256": selected["compatibility_sha256"], "oauth_vault": {"schema_version": 1}},
    }
    return selected, manifest


def authority_sha256(selected: dict, commit: str) -> str:
    # Written out here rather than taken from operations.stack, so the test
    # pins the digest the launcher names its override with.
    body = {**selected, "gitops_commit": commit}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class Host:
    """A fake host: answers reads from ``files`` and records every command."""

    def __init__(self, target, files: dict[str, str], identity: dict) -> None:
        self.target = target
        self.files = files
        self.identity = identity
        self.commands: list[list[str]] = []

    def run(self, command, *, check=True, input_text=None):
        self.commands.append(command)
        if command[0] == "cat":
            if command[1] in self.files:
                return subprocess.CompletedProcess(command, 0, self.files[command[1]], "")
            return subprocess.CompletedProcess(command, 1, "", "No such file")
        if command[:2] == ["docker", "compose"] and command[-3:] == ["config", "--format", "json"]:
            return subprocess.CompletedProcess(command, 0, json.dumps({"services": {
                self.target.value["services"]["mcp"]: {"image": MCP_IMAGE},
                "odoo": {"image": "odoo"},
            }}), "")
        if command[:2] == ["docker", "ps"]:
            return subprocess.CompletedProcess(command, 0, "abc123\n", "")
        if command[:2] == ["docker", "inspect"]:
            return subprocess.CompletedProcess(command, 0, MCP_IMAGE + "\n", "")
        if command[0] == "curl":
            return subprocess.CompletedProcess(command, 0, json.dumps({
                "schema": "usl-odoo-mcp-readiness/v1", "status": "ready", "server_version": "1.4.0",
                "targets": 1, "oauth": {"status": "ready", "schema_version": 1},
            }), "")
        if command[:2] == ["docker", "compose"] and "usl-sign-readiness/v1" in command[-1]:
            return subprocess.CompletedProcess(command, 0, json.dumps({
                "schema": "usl-sign-readiness/v1", "status": "ready",
                "step_ca": {"status": "ok", "trust_sha256": "e" * 64},
                "dss": {"status": "ok", "engine_version": "6.4", "trust_sha256": "f" * 64},
            }), "")
        if check:
            raise AssertionError(f"unexpected command reached the host: {command}")
        return subprocess.CompletedProcess(command, 1, "", "")


def deployed_production(*, override_last: bool = True, override_image: str = MCP_IMAGE):
    target = load_target("production", TARGETS)
    selected, manifest = ledger("production")
    state = target.value["state_directory"]
    override = f"{state}/authorities/mcp-{authority_sha256(selected, GITOPS_COMMIT)}.json"
    working = f"{RUN_ROOT}/komodo/stacks/prod/usl-odoo-production"
    compose_files = [
        f"{working}/compose.yaml",
        f"{state}/generations/{GENERATION}/compose.generation.json",
    ]
    compose_files = [*compose_files, override] if override_last else [override, *compose_files]
    identity = {
        "project": target.project, "working_directory": working,
        "environment_file": "/opt/usl-odoo/production.env",
        "profiles": target.value["compose"]["profiles"],
        "anchor_service": "odoo", "compose_files": compose_files,
    }
    files = {
        f"{RUN_ROOT}/.usl-gitops-commit": GITOPS_COMMIT + "\n",
        f"{RUN_ROOT}/komodo/releases/usl-odoo-production-mcp.json": json.dumps(selected),
        f"{RUN_ROOT}/komodo/releases/usl-odoo-production-mcp-manifest.json": json.dumps(manifest),
        override: json.dumps({"services": {target.value["services"]["mcp"]: {"image": override_image}}}),
    }
    return target, Host(target, files, identity), identity, override


def launcher_environment_absent():
    return mock.patch.dict(
        os.environ,
        {key: value for key, value in os.environ.items() if not key.startswith("USL_RELEASE_GITOPS_")},
        clear=True,
    )


class CommandClassificationTests(unittest.TestCase):
    """The guard itself: every write the stack code can issue is refused."""

    WRITES = (
        ["install", "-d", "-m", "0700", "/var/lib/usl-odoo/runtime/production/authorities"],
        ["python3", "-c", "import pathlib", "/x", "0444"],
        ["rm", "-f", "/var/lib/usl-odoo/runtime/production/active.json"],
        ["mv", "/a", "/b"],
        ["mkdir", "-p", "/x"],
        ["sh", "-c", "cat /etc/hostname"],
        ["flock", "--nonblock", "/x", "sh", "-c", "true"],
        ["docker", "pull", MCP_IMAGE],
        ["docker", "exec", "abc", "step", "ca", "health"],
        ["docker", "rm", "-f", "abc"],
        ["docker", "volume", "rm", "abc"],
        ["docker", "volume", "create", "abc"],
        ["docker", "network", "connect", "a", "b"],
        ["docker", "image", "rm", MCP_IMAGE],
        ["docker", "run", "--rm", MCP_IMAGE, "sh"],
        ["docker", "run", "--rm", "--network", "none", "--read-only", "--entrypoint", "cat", "mutable:tag", "/p"],
        ["docker", "compose", "--project-name", "p", "--file", "/c.yaml", "up", "--detach"],
        ["docker", "compose", "--project-name", "p", "stop", "odoo"],
        ["docker", "compose", "--project-name", "p", "down"],
        ["docker", "compose", "--project-name", "p", "run", "--rm", "odoo"],
        ["docker", "compose", "--project-name", "p", "exec", "--no-TTY", "odoo", "sh", "-c", "x"],
        ["docker", "compose", "--project-name", "p", "exec", "--no-TTY", "db", "psql", "--command",
         "UPDATE res_users SET active = false"],
        ["docker", "compose", "--project-name", "p", "exec", "--no-TTY", "db", "psql", "--command",
         "SELECT 1; DELETE FROM res_users"],
        ["curl", "-X", "POST", "http://127.0.0.1/"],
        ["curl", "--request=DELETE", "http://127.0.0.1/"],
        ["curl", "--data", "x", "http://127.0.0.1/"],
        ["curl", "--output", "/etc/passwd", "http://127.0.0.1/"],
        ["curl", "--output=/tmp/x", "http://127.0.0.1/"],
        ["systemctl", "restart", "docker"],
    )

    READS = (
        ["cat", "/var/lib/usl-odoo/runtime/production/active.json"],
        ["test", "-f", "/x"],
        ["df", "--output=source,avail", "--block-size=1", "/srv"],
        ["findmnt", "--target", "/srv", "--noheadings"],
        ["containerd", "config", "dump"],
        ["docker", "ps", "-a", "--filter", "label=x"],
        ["docker", "inspect", "abc", "--format", "{{.Image}}"],
        ["docker", "image", "inspect", MCP_IMAGE],
        ["docker", "volume", "inspect", "v", "--format", "{{json .Labels}}"],
        ["docker", "info", "--format", "{{.DockerRootDir}}"],
        ["docker", "logs", "--since", "x", "abc"],
        ["docker", "run", "--rm", "--network", "none", "--read-only", "--entrypoint", "cat", MCP_IMAGE, "/p"],
        ["docker", "compose", "--project-name", "p", "--file", "/c.yaml", "ps", "--all", "--format", "json"],
        ["docker", "compose", "--project-name", "p", "config", "--services"],
        ["docker", "compose", "--project-name", "p", "exec", "--no-TTY", "db", "psql", "--command",
         "\nSELECT json_build_object('a', 1)::text\n"],
        ["docker", "compose", "--project-name", "p", "exec", "--no-TTY", "odoo", "python", "-c", "print(1)"],
        ["curl", "--silent", "--output", "/dev/null", "--write-out", "%{http_code}", "http://127.0.0.1/"],
    )

    def test_writes_are_refused(self) -> None:
        for command in self.WRITES:
            with self.subTest(command=command):
                self.assertIsNotNone(read_only_command_problem(command))

    def test_reads_are_allowed(self) -> None:
        for command in self.READS:
            with self.subTest(command=command):
                self.assertIsNone(read_only_command_problem(command))

    def test_standard_input_is_refused(self) -> None:
        self.assertIsNotNone(read_only_command_problem(["cat", "/x"], input_text="secret"))

    def test_refusal_happens_before_the_host_sees_the_command(self) -> None:
        inner = mock.Mock()
        runner = ReadOnlyRunner(inner)
        with self.assertRaisesRegex(RuntimeError, "observation refused a write-capable command: install"):
            runner.run(["install", "-d", "/x"])
        with self.assertRaisesRegex(RuntimeError, "observation refused a write-capable command: flock"):
            with runner.advisory_lock("/x"):
                pass
        inner.run.assert_not_called()

    def test_remote_writer_is_refused(self) -> None:
        target = load_target("production", TARGETS)
        inner = mock.Mock()
        with self.assertRaisesRegex(RuntimeError, "observation refused"):
            _write_remote(target, ReadOnlyRunner(inner), "/x", "content", "0444")
        inner.run.assert_not_called()

    def test_reviewed_read_has_exactly_one_caller(self) -> None:
        # The escape hatch for the announcement audit must stay a single,
        # reviewed call site; a second one needs its own review.
        callers = []
        for path in (ROOT / "operations").rglob("*.py"):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if "run_reviewed_read" in line and "def run_reviewed_read" not in line:
                    callers.append(f"{path.relative_to(ROOT)}:{number}")
        self.assertEqual(len(callers), 2, callers)  # the getattr site and its comment
        self.assertTrue(all(caller.startswith("operations/stack.py:") for caller in callers), callers)


class ObservationModeTests(unittest.TestCase):
    def test_targets_hand_out_read_only_runners_while_observing(self) -> None:
        target = load_target("production", TARGETS)
        self.assertNotIsInstance(target.runner(), ReadOnlyRunner)
        with observation_only():
            self.assertIsInstance(target.runner(), ReadOnlyRunner)
        self.assertNotIsInstance(target.runner(), ReadOnlyRunner)

    def test_wrapper_runs_every_observation_in_observation_mode(self) -> None:
        loader = importlib.machinery.SourceFileLoader(
            "usl_stack_observe_mode", str(ROOT / "scripts/usl-stack-observe"),
        )
        spec = importlib.util.spec_from_loader(loader.name, loader)
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)
        seen = []
        with mock.patch.object(module, "main", side_effect=lambda: seen.append(runtime._OBSERVATION_ONLY) or 0):
            for observation in module.OBSERVATIONS:
                self.assertEqual(module.run(["production", observation]), 0)
        self.assertEqual(seen, [True] * len(module.OBSERVATIONS))


class ObservedAuthorityTests(unittest.TestCase):
    def evidence(self, target, host, identity):
        with (
            launcher_environment_absent(),
            mock.patch(
                "operations.stack._with_mcp_runtime_authority",
                side_effect=AssertionError("observation provisioned MCP authority"),
            ),
        ):
            return _runtime_admission_evidence(target, ReadOnlyRunner(host), identity)

    def assert_no_write_reached_the_host(self, host) -> None:
        for command in host.commands:
            self.assertIsNone(read_only_command_problem(command), command)

    def test_deployed_authority_is_verified_without_launcher_context(self) -> None:
        target, host, identity, override = deployed_production()
        evidence = self.evidence(target, host, identity)
        observation = evidence["mcp_runtime"]["observation"]
        self.assertEqual(observation["source"], "deployed-gitops-run")
        self.assertEqual(observation["gitops_commit"], GITOPS_COMMIT)
        self.assertEqual(observation["override"], override)
        self.assertEqual(evidence["mcp_runtime"]["image"], MCP_IMAGE)
        self.assertEqual(
            evidence["mcp_runtime"]["authority_sha256"],
            authority_sha256(ledger("production")[0], GITOPS_COMMIT),
        )
        self.assert_no_write_reached_the_host(host)

    def test_override_that_is_not_final_fails_closed(self) -> None:
        target, host, identity, _override = deployed_production(override_last=False)
        with self.assertRaisesRegex(RuntimeError, "is not the final Compose file"):
            self.evidence(target, host, identity)
        self.assert_no_write_reached_the_host(host)

    def test_override_with_another_image_fails_closed(self) -> None:
        target, host, identity, _override = deployed_production(
            override_image="ghcr.io/unstaticlabs/odoo-mcp@sha256:" + "9" * 64,
        )
        with self.assertRaisesRegex(RuntimeError, "override is unreadable or differs"):
            self.evidence(target, host, identity)

    def test_ledger_of_another_commit_fails_closed(self) -> None:
        target, host, identity, _override = deployed_production()
        host.files[f"{RUN_ROOT}/.usl-gitops-commit"] = "5" * 40 + "\n"
        with self.assertRaisesRegex(RuntimeError, "commit marker differs"):
            self.evidence(target, host, identity)

    def test_unpinned_working_directory_reports_missing_identity_without_writing(self) -> None:
        target, host, identity, _override = deployed_production()
        identity = {**identity, "working_directory": "/opt/somewhere"}
        with self.assertRaisesRegex(RuntimeError, "exact GitOps identity is missing: .*never provisions"):
            self.evidence(target, host, identity)
        self.assertEqual(host.commands, [])

    def test_launcher_path_keeps_provisioning_authority(self) -> None:
        # A release still writes and appends the override; only observers
        # stopped doing so.
        target, host, identity, _override = deployed_production()
        provisioned = {**identity, "compose_files": [*identity["compose_files"], "/authority.json"]}
        with (
            mock.patch("operations.stack._mcp_runtime_authority", return_value={"image": MCP_IMAGE, "sha256": "0" * 64}),
            mock.patch("operations.stack._with_mcp_runtime_authority", return_value=provisioned) as provision,
            mock.patch("operations.stack._runtime_images", return_value={target.value["services"]["mcp"]: MCP_IMAGE}),
        ):
            evidence = _runtime_admission_evidence(target, host, identity)
        provision.assert_called_once()
        self.assertNotIn("observation", evidence["mcp_runtime"])

    def test_provisioning_reached_by_an_observer_is_refused(self) -> None:
        target, host, identity, _override = deployed_production()

        class Services(Host):
            def run(self, command, *, check=True, input_text=None):
                if command[-2:] == ["config", "--services"]:
                    self.commands.append(command)
                    return subprocess.CompletedProcess(command, 0, "odoo\nodoo-mcp\n", "")
                return super().run(command, check=check, input_text=input_text)

        host = Services(target, host.files, identity)
        with self.assertRaisesRegex(RuntimeError, "observation refused a write-capable command: install"):
            _with_mcp_runtime_authority(
                target, ReadOnlyRunner(host), identity, {"image": MCP_IMAGE, "sha256": "0" * 64},
            )
        self.assertFalse(any(command[0] == "install" for command in host.commands))


def admission_receipt(**changes) -> dict:
    target = load_target("production", TARGETS)
    body = {
        "schema": "usl-release-admission/v2",
        "target": target.name,
        "attempt": "intent-" + "a" * 48,
        "release": RELEASE_IDENTITY,
        "snapshot": "2" * 64,
        "generation": GENERATION,
        "operation_bundle_sha256": "3" * 64,
        "health_sha256": "4" * 64,
        "smoke_sha256": "5" * 64,
        "control_validation_sha256": "6" * 64,
        "runtime_evidence_sha256": None,
        "admitted_at": "2026-11-08T17:47:08Z",
        "status": "admitted",
    }
    body.update({key: value for key, value in changes.items() if key != "sha256"})
    body["sha256"] = changes.get("sha256") or hashlib.sha256(
        json.dumps({key: value for key, value in body.items() if key != "sha256"},
                   sort_keys=True, separators=(",", ":")).encode(),
    ).hexdigest()
    return body


class AdmissionReceiptTests(unittest.TestCase):
    def observe(self, receipt: dict | None, *, generation: str = GENERATION):
        target = load_target("production", TARGETS)
        path = f"{target.value['state_directory']}/generations/{generation}/admission.json"
        files = {} if receipt is None else {path: json.dumps(receipt)}
        host = Host(target, files, {})
        with mock.patch(
            "operations.stack._release", return_value=({"identity": RELEASE_IDENTITY}, "0" * 64, "{}"),
        ):
            result = _observed_admission_receipt(target, ReadOnlyRunner(host), {"generation": generation})
        return result, host

    def test_exact_receipt_is_verified(self) -> None:
        receipt = admission_receipt()
        result, host = self.observe(receipt)
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["sha256"], receipt["sha256"])
        self.assertEqual([command[0] for command in host.commands], ["cat"])

    def test_receipt_for_another_release_fails_closed(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "identity differs"):
            self.observe(admission_receipt(release="7" * 64))

    def test_tampered_receipt_digest_fails_closed(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "digest differs"):
            self.observe(admission_receipt(sha256="8" * 64))

    def test_receipt_not_admitted_fails_closed(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "identity differs"):
            self.observe(admission_receipt(status="quarantined"))

    def test_receipt_of_another_generation_fails_closed(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "names another generation"):
            self.observe(admission_receipt(generation="grel-ffffffffffffffffffff"))

    def test_missing_receipt_is_reported(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "admission receipt is unavailable"):
            self.observe(None)


if __name__ == "__main__":
    unittest.main()
