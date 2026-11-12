"""Wake must restore the recorded cohort, even if failed containers remain."""
from __future__ import annotations

import json
import os
import subprocess
import unittest
from types import SimpleNamespace
from contextlib import contextmanager
from datetime import UTC, datetime
from unittest import mock

from operations.runtime import RuntimeError
from operations import staging_lifecycle as lifecycle
from operations.stack import (
    GATEWAY_MAINTENANCE_MARKER, RELEASE_OPERATION_LOCK, _mcp_authority_from_gitops, _runtime_start_authority,
    _start_runtime, wake_staging,
)
from scripts.tests.test_release_manifest import manifest


class CohortHost:
    """Model Compose's no-recreate semantics and immutable container resources."""

    def __init__(self, *, stale=True, running=False):
        self.target = SimpleNamespace(name="staging", project="qa-staging", value={
            "environment": "local", "state_directory": "/runtime/staging",
            "compose": {"resource_overlay": None, "anchor_service": "odoo"},
            "services": {"odoo": "odoo", "odoo_db": "db", "mcp": "mcp"},
            "lifecycle": {"mode": "on_demand", "wake_timeout_seconds": 1},
        })
        self.identity = {
            "project": "qa-staging", "working_directory": "/retained/base-b",
            "environment_file": "/qa.env", "compose_files": ["/retained/base-b/compose.json"],
            "profiles": [], "anchor_service": "odoo",
        }
        self.active = {
            "generation": "gadmitted-a", "network": "qa-a",
            "volumes": {"odoo_postgres": "qa-data-a"},
            "release_manifest": "/runtime/staging/generations/gadmitted-a/usl-release.json",
        }
        self.config = {
            "services": {
                "odoo": {"image": "qa-odoo:a", "networks": {"default": {}}, "volumes": []},
                "db": {"image": "qa-db:a", "networks": {"default": {}}, "volumes": [
                    {"type": "volume", "source": "postgres-data", "target": "/data", "read_only": False},
                ]},
                "mcp": {"image": "qa-mcp:independent", "networks": {"default": {}}, "volumes": []},
            },
            "volumes": {"postgres-data": {"name": "qa-data-a", "external": True}},
            "networks": {"default": {"name": "qa-a", "external": True}},
        }
        self.files = {self.active["release_manifest"]: json.dumps(manifest())}
        self.commands = []
        self.serial = 0
        self.locks = set()
        self.dirs = set()
        self.containers = {}
        self.refuse_recreate = False
        for name in self.config["services"]:
            self.containers[name] = self.make_container(name, "b" if stale else "a", running)

    def make_container(self, name, cohort, running=True):
        self.serial += 1
        service = self.config["services"][name]
        mounts = [] if name != "db" else [{
            "Type": "volume", "Name": f"qa-data-{cohort}", "Destination": "/data", "RW": True,
        }]
        image = service["image"] if name == "mcp" else f"qa-{name if name != 'odoo' else 'odoo'}:{cohort}"
        return {
            "Id": f"container-{self.serial}", "Image": f"id-{image}",
            "Config": {"Image": image, "Labels": {
                "com.docker.compose.project": self.target.project,
                "com.docker.compose.service": name,
                "com.docker.compose.config-hash": f"hash-{name}-{cohort}",
            }},
            "Mounts": mounts, "NetworkSettings": {"Networks": {f"qa-{cohort}": {}}},
            "State": {"Status": "running" if running else "exited", "Running": running},
        }

    def runtime(self):
        return {"compose": self.identity, "active_state": self.active, "generation": "gadmitted-a"}

    @contextmanager
    def advisory_lock(self, path, **kwargs):
        if path in self.locks:
            raise RuntimeError("another operation owns advisory lock: " + path)
        self.locks.add(path)
        try:
            yield
        finally:
            self.locks.remove(path)

    def run(self, command, *, check=True, input_text=None):
        self.commands.append(command)
        output = ""
        code = 0
        if command[0] == "test":
            if command[1] == "-e":
                code = 0 if command[2] in self.files or command[2] in self.dirs else 1
            elif command[1] == "!":
                code = 1 if command[3] in self.files or command[3] in self.dirs else 0
        elif command[0] == "cat":
            output = self.files.get(command[1], "")
            code = 0 if command[1] in self.files else 1
        elif command[0] == "mkdir":
            code = 1 if command[1] in self.dirs else 0
            self.dirs.add(command[1])
        elif command[0] == "rm":
            self.files.pop(command[-1], None)
        elif command[0] == "rmdir":
            self.dirs.remove(command[-1])
        elif command[0] == "find":
            code = 1
        elif command[0] == "install":
            pass
        elif command[0] == "python3":
            self.files[command[3]] = input_text
        elif command[:2] == ["docker", "compose"]:
            if "config" in command:
                if "--services" in command:
                    output = "\n".join(self.config["services"])
                elif "--hash" in command:
                    output = "\n".join(f"{name} hash-{name}-a" for name in self.config["services"])
                else:
                    output = json.dumps(self.config)
            elif "up" in command:
                names = command[command.index("--no-deps") + 1:]
                for name in names:
                    if name not in self.containers or ("--no-recreate" not in command and not self.refuse_recreate):
                        self.containers[name] = self.make_container(name, "a")
                    self.containers[name]["State"] = {"Status": "running", "Running": True}
            elif "stop" in command:
                for value in self.containers.values():
                    value["State"] = {"Status": "exited", "Running": False}
            else:
                raise AssertionError(command)
        elif command[:2] == ["docker", "ps"]:
            name = next(item.split("=", 2)[2] for item in command if item.startswith("label=com.docker.compose.service="))
            value = self.containers.get(name)
            output = (value["State"]["Status"] if "{{.State}}" in command else value["Id"]) if value else ""
        elif command[:3] == ["docker", "image", "inspect"]:
            output = "id-" + command[3]
        elif command[:2] == ["docker", "inspect"]:
            value = next(item for item in self.containers.values() if item["Id"] == command[2])
            template = command[command.index("--format") + 1]
            if template == "{{json .}}":
                output = json.dumps(value)
            else:
                output = json.dumps([
                    value["Config"]["Image"], value["Image"], value["Config"]["Labels"]["com.docker.compose.config-hash"],
                    value["Mounts"], value["NetworkSettings"]["Networks"], value["State"]["Status"],
                ])
        else:
            raise AssertionError(command)
        return subprocess.CompletedProcess(command, code, output, "")


class RuntimeReconciliationTests(unittest.TestCase):
    def test_admitted_a_replaces_stale_b_without_starting_desired_c(self):
        host = CohortHost()
        _start_runtime(host.target, host, host.runtime(), mcp_authority=False)
        self.assertEqual(host.containers["odoo"]["Config"]["Image"], "qa-odoo:a")
        self.assertEqual(host.containers["db"]["Mounts"][0]["Name"], "qa-data-a")
        self.assertEqual(set(host.containers["odoo"]["NetworkSettings"]["Networks"]), {"qa-a"})
        self.assertEqual(host.containers["mcp"]["Config"]["Image"], "qa-mcp:independent")
        self.assertEqual(host.active["generation"], "gadmitted-a")

    def test_matching_sleeping_containers_keep_their_ids(self):
        host = CohortHost(stale=False)
        before = {name: value["Id"] for name, value in host.containers.items()}
        _start_runtime(host.target, host, host.runtime(), mcp_authority=False)
        self.assertEqual({name: value["Id"] for name, value in host.containers.items()}, before)
        self.assertTrue(all(value["State"]["Running"] for value in host.containers.values()))

    def test_network_drift_is_repaired_even_with_matching_compose_hash(self):
        host = CohortHost(stale=False)
        host.containers["odoo"]["NetworkSettings"]["Networks"] = {"qa-b": {}}
        before = {name: value["Id"] for name, value in host.containers.items()}
        _start_runtime(host.target, host, host.runtime(), mcp_authority=False)
        self.assertEqual(set(host.containers["odoo"]["NetworkSettings"]["Networks"]), {"qa-a"})
        self.assertNotEqual(host.containers["odoo"]["Id"], before["odoo"])
        self.assertNotEqual(host.containers["db"]["Id"], before["db"])

    def test_missing_service_is_created_in_a_consistent_cohort(self):
        host = CohortHost(stale=False)
        del host.containers["mcp"]
        before = host.containers["db"]["Id"]
        _start_runtime(host.target, host, host.runtime(), mcp_authority=False)
        self.assertIn("mcp", host.containers)
        self.assertNotEqual(host.containers["db"]["Id"], before)

    def test_legacy_v2_generation_preserves_start_without_a_new_mcp_contract(self):
        from scripts.tests.test_usl_stack_runtime import release_manifest
        host = CohortHost()
        host.files[host.active["release_manifest"]] = json.dumps(release_manifest("usl-release/v2"))
        del host.containers["mcp"]
        before = {name: value["Id"] for name, value in host.containers.items()}
        _start_runtime(host.target, host, host.runtime(), mcp_authority=False)
        self.assertEqual({name: host.containers[name]["Id"] for name in before}, before)
        self.assertIn("mcp", host.containers)

    def test_failed_reconciliation_reports_the_generation_service_and_mismatch(self):
        host = CohortHost()
        host.refuse_recreate = True
        with self.assertRaisesRegex(RuntimeError, "gadmitted-a.*odoo.*image"):
            _start_runtime(host.target, host, host.runtime(), mcp_authority=False)


class WakeReconciliationTests(unittest.TestCase):
    def wake(self, host, **kwargs):
        with (
            mock.patch("operations.stack.inspect_runtime", side_effect=lambda *args: host.runtime()),
            mock.patch("operations.stack._wait_odoo_ready"),
        ):
            return wake_staging(host.target, host, reason="release:qa", **kwargs)

    def state(self, host):
        return json.loads(host.files["/runtime/staging/lifecycle.json"])

    def test_running_stale_b_is_not_accepted_as_already_active(self):
        host = CohortHost(running=True)
        result = self.wake(host)
        self.assertEqual(result["status"], "woken")
        self.assertEqual(host.containers["odoo"]["Config"]["Image"], "qa-odoo:a")
        self.assertEqual(self.state(host)["state"], "active")

    def test_running_matching_cohort_remains_a_noop(self):
        host = CohortHost(stale=False, running=True)
        ids = {key: value["Id"] for key, value in host.containers.items()}
        result = self.wake(host)
        self.assertEqual(result["status"], "already-active")
        self.assertEqual({key: value["Id"] for key, value in host.containers.items()}, ids)
        self.assertFalse(any("up" in command or "stop" in command for command in host.commands))

    def test_maintenance_defers_even_if_stale_b_is_running(self):
        host = CohortHost(running=True)
        host.files[GATEWAY_MAINTENANCE_MARKER] = "{}"
        result = self.wake(host)
        self.assertEqual(result["status"], "deferred")
        self.assertEqual(host.containers["odoo"]["Config"]["Image"], "qa-odoo:b")
        self.assertFalse(any("up" in command for command in host.commands))

    def test_release_may_resume_interrupted_activation_under_its_existing_lock(self):
        host = CohortHost(running=True)
        host.files[GATEWAY_MAINTENANCE_MARKER] = "{}"
        host.files["/runtime/staging/lifecycle.json"] = lifecycle.dumps(lifecycle.new_state(datetime.now(UTC), "waking"))
        host.locks.add(RELEASE_OPERATION_LOCK)
        result = self.wake(host, release_lock_held=True, may_resume=True)
        self.assertEqual(result["status"], "woken")
        self.assertEqual(host.containers["db"]["Mounts"][0]["Name"], "qa-data-a")
        self.assertEqual(host.locks, {RELEASE_OPERATION_LOCK})
        self.assertEqual(host.dirs, set())
        self.assertIn(GATEWAY_MAINTENANCE_MARKER, host.files)

    def test_failed_identity_check_is_recorded_and_later_retry_recovers(self):
        host = CohortHost()
        host.refuse_recreate = True
        with self.assertRaisesRegex(RuntimeError, "gadmitted-a"):
            self.wake(host)
        state = self.state(host)
        self.assertEqual(state["state"], "failed")
        self.assertIn("gadmitted-a", state["last_error"]["message"])
        self.assertIn("image", state["last_error"]["message"])
        self.assertEqual(host.locks, set())
        self.assertEqual(host.dirs, set())
        host.refuse_recreate = False
        self.assertEqual(self.wake(host)["status"], "woken")
        self.assertIsNone(self.state(host)["last_error"])

    def test_adopted_layout_can_bootstrap_missing_mcp_without_v3_authority(self):
        host = CohortHost()
        host.active = None
        del host.containers["mcp"]
        with mock.patch("operations.stack._mcp_runtime_compose_identity", side_effect=AssertionError("adopted bootstrap consulted v3 source")):
            self.assertEqual(self.wake(host)["status"], "woken")
        self.assertIn("mcp", host.containers)

    def test_adopted_layout_keeps_existing_start_semantics(self):
        host = CohortHost()
        host.active = None
        ids = {key: value["Id"] for key, value in host.containers.items()}
        self.assertEqual(self.wake(host)["status"], "woken")
        self.assertEqual({key: value["Id"] for key, value in host.containers.items()}, ids)


class WakeMcpAuthorityTests(unittest.TestCase):
    def documents(self):
        release = manifest()
        contract = release["mcp_contract"]
        image = "ghcr.io/unstaticlabs/odoo-mcp@sha256:" + "e" * 64
        selected = {
            "schema": "usl-odoo-mcp-environment-release/v1", "environment": "staging",
            "commit": "f" * 40, "compatibility_sha256": "c" * 64, "image": image,
            "oauth_vault_schema": 1,
            "release_manifest": "ghcr.io/unstaticlabs/usl-odoo-mcp-release@sha256:" + "d" * 64,
        }
        compatibility = {
            "sha256": "c" * 64, "oauth_vault": {"schema_version": 1},
            "server_version": "1.9.0", "supported_odoo_series": ["19.3"],
            "required_modules": contract["required_modules"], "required_public_methods": contract["public_methods"],
            "required_actions": contract["actions"], "required_agent_identity": contract["agent_identity"],
        }
        documents = {
            "komodo/releases/usl-odoo-staging-mcp.json": selected,
            "komodo/releases/usl-odoo-staging-mcp-manifest.json": {
                "schema": "usl-odoo-mcp-oci-release/v2",
                "source": {"repository": "https://github.com/unstaticlabs/odoo-mcp.git", "ref": "refs/heads/main", "commit": "f" * 40},
                "image": {"digest_reference": image}, "compatibility": compatibility,
            },
        }
        return release, documents

    def test_daemon_restores_independent_compatible_mcp_from_deployed_run(self):
        host = CohortHost()
        host.target.value["environment"] = "staging"
        release, documents = self.documents()
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch("operations.stack._mcp_runtime_compose_identity", side_effect=lambda target, runner, identity: identity),
            mock.patch("operations.stack._observed_mcp_authority", return_value=({
                **_mcp_authority_from_gitops("staging", "a" * 40, lambda relative: documents[relative]),
                "runtime_gitops_root": "/retained/run",
            }, {})),
            mock.patch("operations.stack._load_deployed_gitops_json", side_effect=lambda runner, root, relative: documents[relative]),
        ):
            authority = _runtime_start_authority(host.target, host, host.runtime(), release, pinned=False)
        self.assertEqual(authority["image"], documents["komodo/releases/usl-odoo-staging-mcp.json"]["image"])
        self.assertNotEqual(authority["image"], release["mcp"]["image"])
        self.assertEqual(authority["gitops_commit"], "a" * 40)

    def test_daemon_refuses_mcp_that_requires_more_than_active_odoo_supports(self):
        host = CohortHost()
        host.target.value["environment"] = "staging"
        release, documents = self.documents()
        documents["komodo/releases/usl-odoo-staging-mcp-manifest.json"]["compatibility"]["required_actions"] = ["qa.unavailable_action"]
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch("operations.stack._mcp_runtime_compose_identity", side_effect=lambda target, runner, identity: identity),
            mock.patch("operations.stack._observed_mcp_authority", return_value=({
                **_mcp_authority_from_gitops("staging", "a" * 40, lambda relative: documents[relative]),
                "runtime_gitops_root": "/retained/run",
            }, {})),
            mock.patch("operations.stack._load_deployed_gitops_json", side_effect=lambda runner, root, relative: documents[relative]),
        ):
            with self.assertRaisesRegex(RuntimeError, "required_actions exceed Odoo support"):
                _runtime_start_authority(host.target, host, host.runtime(), release, pinned=False)
        self.assertEqual(host.commands, [])

    def test_missing_independent_mcp_never_borrows_odoo_a_authority(self):
        host = CohortHost()
        host.target.value["environment"] = "staging"
        del host.containers["mcp"]
        release, documents = self.documents()
        old_odoo_authority = {
            **_mcp_authority_from_gitops("staging", "a" * 40, lambda relative: documents[relative]),
            "image": release["mcp"]["image"], "runtime_gitops_root": "/retained/old-odoo-a",
        }
        with (
            mock.patch("operations.stack._observed_mcp_authority", return_value=(old_odoo_authority, {})) as observe,
            mock.patch("operations.stack._load_deployed_gitops_json", side_effect=lambda runner, root, relative: documents[relative]),
        ):
            with self.assertRaisesRegex(RuntimeError, "MCP container is missing.*independently admitted"):
                _runtime_start_authority(host.target, host, host.runtime(), release, pinned=False)
        observe.assert_not_called()
        self.assertFalse(any("up" in command or "stop" in command or command[0] == "python3" for command in host.commands))

    def test_release_wake_retains_actual_mcp_even_with_a_newer_launcher_checkout(self):
        host = CohortHost()
        host.target.value["environment"] = "staging"
        release, documents = self.documents()
        actual = {
            **_mcp_authority_from_gitops("staging", "a" * 40, lambda relative: documents[relative]),
            "runtime_gitops_root": "/retained/actual-mcp",
        }
        with (
            mock.patch.dict(os.environ, {"USL_RELEASE_GITOPS_ROOT": "/desired/newer", "USL_RELEASE_GITOPS_COMMIT": "f" * 40}),
            mock.patch("operations.stack._mcp_runtime_compose_identity", side_effect=lambda target, runner, identity: identity),
            mock.patch("operations.stack._observed_mcp_authority", return_value=(actual, {})),
            mock.patch("operations.stack._load_deployed_gitops_json", side_effect=lambda runner, root, relative: documents[relative]),
            mock.patch("operations.stack._candidate_mcp_authority", side_effect=AssertionError("wake selected a desired MCP release")),
        ):
            self.assertEqual(_runtime_start_authority(host.target, host, host.runtime(), release, pinned=False), actual)

    def test_launcher_uses_its_pinned_compatible_independent_authority(self):
        host = CohortHost()
        host.target.value["environment"] = "production"
        release = manifest()
        authority = {"image": "independent"}
        with mock.patch("operations.stack._candidate_mcp_authority", return_value=authority) as candidate:
            self.assertEqual(_runtime_start_authority(host.target, host, host.runtime(), release, pinned=True), authority)
        candidate.assert_called_once_with(host.target, release)


if __name__ == "__main__":
    unittest.main()
