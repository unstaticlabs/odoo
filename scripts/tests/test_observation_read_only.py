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
        # Forms a looser guard once let through.
        ["curl", "-XPOST", "http://127.0.0.1/"],
        ["curl", "-sd", "x", "http://127.0.0.1/"],
        ["curl", "-o/tmp/x", "http://127.0.0.1/"],
        ["curl", "--data-raw", "x", "http://127.0.0.1/"],
        ["curl", "--data-binary", "@f", "http://127.0.0.1/"],
        ["curl", "-D", "/tmp/h", "http://127.0.0.1/"],
        ["curl", "--dump-header", "/tmp/h", "http://127.0.0.1/"],
        ["curl", "--create-dirs", "--output-dir", "/tmp", "http://127.0.0.1/"],
        ["docker", "compose", "--project-name", "p", "exec", "--no-TTY", "db", "psql", "--username", "u",
         "--dbname", "d", "--tuples-only", "--no-align", "--command",
         "WITH d AS (DELETE FROM res_users RETURNING id) SELECT 1"],
        ["docker", "compose", "--project-name", "p", "exec", "--no-TTY", "db", "psql", "--username", "u",
         "--dbname", "d", "--tuples-only", "--no-align", "--command", "SELECT pg_terminate_backend(42)"],
        ["docker", "compose", "--project-name", "p", "exec", "--no-TTY", "db", "psql", "--username", "u",
         "--dbname", "d", "--tuples-only", "--no-align", "--command", "SELECT 1", "--file", "/tmp/x.sql"],
        ["docker", "compose", "--project-name", "p", "exec", "--no-TTY", "odoo", "python", "-c",
         "import os;os.remove('/x')"],
        ["docker", "compose", "--project-name", "p", "exec", "--no-TTY", "odoo", "python", "-c",
         "import pathlib;pathlib.Path('/x').write_text('y')"],
        ["docker", "compose", "--project-name", "p", "exec", "--no-TTY", "odoo", "python", "-c",
         "open('/x', 'w').close()"],
        ["docker", "compose", "--project-name", "p", "exec", "--no-TTY", "odoo", "python", "-c",
         "import subprocess;subprocess.run(['true'])"],
        ["docker", "compose", "--project-name", "p", "exec", "--no-TTY", "odoo", "python", "-c",
         "import httpx;httpx.post('http://x')"],
        ["docker", "compose", "--project-name", "p", "exec", "--no-TTY", "odoo", "python", "-c",
         "import pathlib;pathlib.Path('/x').open('w')"],
        ["docker", "compose", "--project-name", "p", "exec", "--no-TTY", "odoo", "python", "-c",
         "import pathlib;pathlib.Path('/a').replace('/b')"],
        ["docker", "compose", "--project-name", "p", "exec", "--no-TTY", "odoo", "python", "-c",
         "import io;io.FileIO('/x','w')"],
        ["docker", "compose", "--project-name", "p", "exec", "--no-TTY", "odoo", "python", "-c",
         "import os;getattr(os,'remove')('/x')"],
        ["docker", "compose", "--project-name", "p", "exec", "--no-TTY", "odoo", "python", "-c",
         "import urllib.request;urllib.request.urlopen('http://x', data=b'x')"],
        ["docker", "compose", "--project-name", "p", "exec", "--no-TTY", "odoo", "python", "-c",
         "import urllib.request;urllib.request.Request('http://x/delete', data=b'x')"],
        ["docker", "compose", "--project-name", "p", "exec", "--no-TTY", "odoo", "python", "-c",
         "import http.client;http.client.HTTPConnection('x').request('DELETE','/')"],
        ["date", "-s", "2020-01-01"],
        ["curl", "--write-out", "%output{/tmp/x}%{http_code}", "http://127.0.0.1/"],
        ["docker", "compose", "--project-name", "p", "exec", "--no-TTY", "db", "psql", "--username", "u",
         "--dbname", "d", "--tuples-only", "--no-align", "--command", "SELECT pg_stat_reset()"],
        ["docker", "compose", "--project-name", "p", "exec", "--no-TTY", "db", "psql", "--username", "u",
         "--dbname", "d", "--tuples-only", "--no-align", "--command",
         "SELECT pg_logical_emit_message(true, 'x', 'y')"],
        ["hostname", "evil"],
        ["docker", "compose", "--project-name", "p", "config", "--output", "/etc/x"],
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
        ["docker", "compose", "--project-name", "p", "exec", "--no-TTY", "odoo", "python", "-c",
         "import json,pathlib;print(json.dumps(pathlib.Path('/x').read_text().replace('a','b')))"],
        ["curl", "--silent", "--output", "/dev/null", "--write-out", "%{http_code}", "http://127.0.0.1/"],
    )

    def test_the_stack_sql_probes_are_reads(self) -> None:
        from operations import control_manifest
        from operations import stack

        for name, query in (
            ("ODOO_CONTROL_SQL", control_manifest.ODOO_CONTROL_SQL),
            ("PAPERLESS_CONTROL_SQL", control_manifest.PAPERLESS_CONTROL_SQL),
            ("RELEASE_DEFINITIONS_SQL", control_manifest.RELEASE_DEFINITIONS_SQL),
            ("CRON_INVENTORY_SQL", stack.CRON_INVENTORY_SQL),
        ):
            with self.subTest(query=name):
                command = [
                    "docker", "compose", "--project-name", "p", "exec", "--no-TTY", "db",
                    "psql", "--username", "u", "--dbname", "d", "--tuples-only", "--no-align",
                    "--command", query,
                ]
                self.assertIsNone(read_only_command_problem(command))

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
        # reviewed call site; a second one needs its own review. Code only:
        # comments and docstrings do not count, any reference in code does.
        import ast

        references = []
        for path in (ROOT / "operations").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.name == "run_reviewed_read":
                    continue
                if (
                    (isinstance(node, ast.Attribute) and node.attr == "run_reviewed_read")
                    or (isinstance(node, ast.Constant) and node.value == "run_reviewed_read")
                ):
                    function = next(
                        (
                            parent.name for parent in ast.walk(tree)
                            if isinstance(parent, ast.FunctionDef)
                            and any(child is node for child in ast.walk(parent))
                            and parent.name != "run_reviewed_read"
                        ),
                        None,
                    )
                    references.append((path.relative_to(ROOT).as_posix(), function))
        self.assertEqual(references, [("operations/stack.py", "_audit_announcements")])


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

    def test_launcher_variables_do_not_replace_the_deployed_identity(self) -> None:
        # A launcher checkout can be newer than what runs; the observer must
        # judge the running cohort by the run it was started from.
        target, host, identity, override = deployed_production()
        with (
            mock.patch.dict(os.environ, {
                "USL_RELEASE_GITOPS_ROOT": "/nonexistent", "USL_RELEASE_GITOPS_COMMIT": "6" * 40,
            }),
            mock.patch(
                "operations.stack._with_mcp_runtime_authority",
                side_effect=AssertionError("observation provisioned MCP authority"),
            ),
        ):
            evidence = _runtime_admission_evidence(target, ReadOnlyRunner(host), identity)
        self.assertEqual(evidence["mcp_runtime"]["observation"]["gitops_commit"], GITOPS_COMMIT)

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
    def observe(self, receipt: dict | None, *, generation: str = GENERATION, snapshot: str | None = None):
        target = load_target("production", TARGETS)
        path = f"{target.value['state_directory']}/generations/{generation}/admission.json"
        files = {} if receipt is None else {path: json.dumps(receipt)}
        host = Host(target, files, {})
        runtime_value = {"generation": generation, "active_state": {"snapshot": snapshot or "2" * 64}}
        with mock.patch(
            "operations.stack._release", return_value=({"identity": RELEASE_IDENTITY}, "0" * 64, "{}"),
        ):
            result = _observed_admission_receipt(target, ReadOnlyRunner(host), runtime_value)
        return result, host

    def test_receipt_for_another_snapshot_fails_closed(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "another snapshot"):
            self.observe(admission_receipt(), snapshot="9" * 64)

    def test_adopted_cohort_is_not_reported_as_admitted(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "adopted and has no generation admission receipt"):
            self.observe(None, generation="adopted")

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


class ObservationCommandTests(unittest.TestCase):
    """Drive the real smoke and health handlers through the read-only runner."""

    def deployed(self):
        target, host, identity, override = deployed_production()
        state = target.value["state_directory"]
        host.files[f"{state}/generations/{GENERATION}/admission.json"] = json.dumps(admission_receipt())
        containers = []
        project = target.project
        for service in target.value["services"].values():
            networks = f"{project}_default"
            if service == target.value["services"]["receipt_fetcher"]:
                networks = f"{project}_receipt-proxy"
            elif service == target.value["services"]["receipt_egress"]:
                networks = f"{project}_receipt-proxy,{project}_receipt-public"
            containers.append({"Service": service, "State": "running", "Health": "healthy", "Networks": networks})
        status = {
            "containers": containers, "compose": identity, "generation": GENERATION,
            "active_state": {"snapshot": "2" * 64},
        }
        release = {
            "identity": RELEASE_IDENTITY,
            "components": {
                "receipt-fetcher": {}, "receipt-egress": {},
                "backup-tool": {"digest_reference": "ghcr.io/unstaticlabs/usl-odoo-backup@sha256:" + "7" * 64},
            },
        }
        return target, host, status, release

    def recording_host(self, target, host):
        from operations import control_manifest
        from scripts.tests.test_usl_stack_health import HealthRunner

        health = HealthRunner(target)
        answers = {
            control_manifest.ODOO_CONTROL_SQL: {
                "ledger_delta": 0, "companies": 1, "users": 2, "moves": 3, "attachments": 4,
                "stored_attachments": 4, "failed_mail": 0, "failed_documents": 0, "bank_failed": 0,
                "payment_failed": 0, "sign_archive_failed": 0, "cron_failures": 0, "cron_lag": [],
            },
            control_manifest.PAPERLESS_CONTROL_SQL: {"documents": 1, "with_ocr": 1, "missing_original_name": 0},
            control_manifest.RELEASE_DEFINITIONS_SQL: {"acl": [], "rules": [], "crons": [], "groups": []},
        }

        class Recording(Host):
            def run(self, command, *, check=True, input_text=None):
                if command[:2] == ["docker", "compose"] and "psql" in command:
                    self.commands.append(command)
                    query = command[-1]
                    value = answers.get(query, {"installed": [], "active": [], "invalid_identity_count": 0})
                    return subprocess.CompletedProcess(command, 0, json.dumps(value), "")
                if command[:2] == ["docker", "compose"] and "python" in command:
                    program = command[-1]
                    if "filestore" in program:
                        self.commands.append(command)
                        return subprocess.CompletedProcess(command, 0, json.dumps({"files": 9}), "")
                    if "originals" in program:
                        self.commands.append(command)
                        return subprocess.CompletedProcess(command, 0, json.dumps(
                            {"originals": 1, "archive": 1, "thumbnails": 1, "tantivy": 1, "vectors": 1},
                        ), "")
                if command[:3] == ["docker", "image", "inspect"]:
                    self.commands.append(command)
                    return subprocess.CompletedProcess(command, 0, "[]", "")
                if command[:2] == ["docker", "run"]:
                    self.commands.append(command)
                    return subprocess.CompletedProcess(command, 0, "{}", "")
                # Health's own probes (endpoints, config, mTLS, containment,
                # Ollama) are answered by the existing health fixture.
                compose_probe = (
                    command[:2] == ["docker", "compose"]
                    and "usl-sign-readiness/v1" not in command[-1]
                    and command[-3:] != ["config", "--format", "json"]
                )
                endpoint_probe = command[0] == "curl" and "--fail" not in command
                if compose_probe or endpoint_probe:
                    self.commands.append(command)
                    return health.run(command, check=check)
                return super().run(command, check=check, input_text=input_text)

        return Recording(target, host.files, host.identity)

    def run_command(self, handler):
        import argparse
        import io
        from contextlib import redirect_stdout

        target, host, status, release = self.deployed()
        host = self.recording_host(target, host)
        output = io.StringIO()
        with (
            observation_only(),
            launcher_environment_absent(),
            mock.patch.object(type(target), "runner", lambda _self: ReadOnlyRunner(host)),
            mock.patch("operations.stack.load_target", return_value=target),
            mock.patch("operations.stack.inspect_runtime", return_value=status),
            mock.patch("operations.stack._release", return_value=(release, "0" * 64, "{}")),
            mock.patch("operations.stack.parse_cron_policy", return_value={}),
            mock.patch("operations.stack.validate_cron_runtime", return_value={"status": "passed"}),
            mock.patch(
                "operations.stack._with_mcp_runtime_authority",
                side_effect=AssertionError("observation provisioned MCP authority"),
            ),
            redirect_stdout(output),
        ):
            code = handler(argparse.Namespace(target="production", targets=TARGETS, json=True))
        return code, json.loads(output.getvalue()), host

    def assert_only_reads(self, host) -> None:
        self.assertTrue(host.commands)
        for command in host.commands:
            self.assertIsNone(read_only_command_problem(command), command)

    def test_smoke_observes_authority_and_admission_with_reads_only(self) -> None:
        from operations.stack import smoke_command

        code, report, host = self.run_command(smoke_command)
        self.assertEqual((code, report["status"], report["failures"]), (0, "passed", []))
        self.assertEqual(report["admission"]["status"], "verified")
        self.assertEqual(report["services"]["mcp_runtime"]["observation"]["source"], "deployed-gitops-run")
        self.assert_only_reads(host)

    def test_health_observes_authority_with_reads_only(self) -> None:
        from operations.stack import health_command

        code, report, host = self.run_command(health_command)
        self.assertEqual((code, report["status"], report["failures"]), (0, "passed", []))
        self.assertEqual(report["services"]["mcp_runtime"]["observation"]["gitops_commit"], GITOPS_COMMIT)
        self.assert_only_reads(host)


if __name__ == "__main__":
    unittest.main()
