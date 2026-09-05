from __future__ import annotations

import copy
import json
import subprocess
import time
import tempfile
import shutil
import unittest
from unittest.mock import patch
from pathlib import Path

from operations.runtime import (
    CommandRunner,
    RuntimeError,
    compose_command,
    compose_identity,
    inspect_runtime,
    load_target,
    validate_target,
    validate_secret_text,
)
from operations.stack import (
    _cleanup_adoption_candidate_anchor,
    _validate_mcp_readiness,
    _validate_sign_readiness,
    runtime_command,
)
from scripts.tests.test_release_manifest import manifest as v3_release_manifest


ROOT = Path(__file__).resolve().parents[2]
TARGETS = ROOT / "operations/targets"
HOST_TARGETS = ROOT / "operations/targets-host"


class CommandRunnerSecretTests(unittest.TestCase):
    def test_stdin_content_is_absent_from_local_and_ssh_failure_errors(self) -> None:
        secret = "stdin-only-secret-sentinel"
        failed = subprocess.CompletedProcess([], 9, "", f"writer echoed {secret}")
        for prefix in ((), ("ssh", "production", "--")):
            with self.subTest(prefix=prefix), patch(
                "operations.runtime.subprocess.run", return_value=failed,
            ) as run:
                with self.assertRaises(RuntimeError) as caught:
                    CommandRunner(prefix).run(
                        ["python3", "-c", "raise SystemExit(9)", "/proof/secret", "0600"],
                        input_text=secret,
                    )
                self.assertNotIn(secret, str(caught.exception))
                self.assertNotIn(secret, " ".join(run.call_args.args[0]))
                self.assertEqual(run.call_args.kwargs["input"], secret)

    def test_deadline_interrupts_a_blocking_command_without_echoing_stdin(self) -> None:
        secret = "deadline-secret-sentinel"
        runner = CommandRunner(deadline_monotonic=time.monotonic() + 1)
        with patch(
            "operations.runtime.subprocess.run",
            side_effect=subprocess.TimeoutExpired(["writer"], 1),
        ):
            with self.assertRaisesRegex(RuntimeError, "operation deadline") as caught:
                runner.run(["writer", "/proof/secret"], input_text=secret)
        self.assertNotIn(secret, str(caught.exception))

    def test_runtime_status_does_not_install_recovery_deadline(self) -> None:
        target = load_target("local", TARGETS)
        runner = target.runner()
        target = type("TargetWithRunner", (), {
            "runner": lambda _self: runner, "name": target.name, "value": target.value,
        })()
        arguments = type("Arguments", (), {
            "target": "local", "targets": TARGETS, "action": "status", "json": True,
        })()
        with (
            patch("operations.stack.load_target", return_value=target),
            patch("operations.stack.inspect_runtime", return_value={}),
            patch("builtins.print"),
        ):
            self.assertEqual(runtime_command(arguments), 0)
        self.assertIsNone(runner.deadline_monotonic)

    @unittest.skipUnless(shutil.which("flock"), "host does not provide flock")
    def test_advisory_lock_is_released_when_owner_exits(self) -> None:
        path = str(Path(tempfile.mkdtemp()) / "proof.lock")
        runner = CommandRunner()
        with runner.advisory_lock(path):
            with self.assertRaisesRegex(RuntimeError, "another operation"):
                with CommandRunner().advisory_lock(path):
                    pass
        with CommandRunner().advisory_lock(path):
            pass


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
                "com.docker.compose.service": self.target.value["compose"]["anchor_service"],
                "com.docker.compose.project.config_files": "/release/compose.yaml,/release/production.yaml",
                "com.docker.compose.project.working_dir": "/release",
                "com.docker.compose.project.environment_file": (
                    "/runtime/site.env,/release/.env"
                ),
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


def active_generation(target) -> dict:
    generation = "g20260901-a1b2c3d4"
    return {
        "schema": "usl-active-generation/v1",
        "target": target.name,
        "generation": generation,
        "volumes": {
            role: f"generation-{role}"
            for role in target.value["volumes"]
        },
        "network": "generation-network",
        "snapshot": "a" * 64,
        "release_manifest": (
            target.value["state_directory"]
            + f"/generations/{generation}/usl-release.json"
        ),
        "previous": {},
    }


def release_manifest(schema: str) -> dict:
    value = copy.deepcopy(v3_release_manifest())
    if schema == "usl-release/v3":
        return value
    for component in value["components"].values():
        component.pop("attestations")
    return {
        "schema": "usl-release/v2",
        "source": {
            "repository": value["source"]["repository"],
            "commit": value["source"]["commit"],
        },
        "components": value["components"],
        "mcp": {
            key: value["mcp"][key]
            for key in ("repository", "ref", "commit", "image", "compatibility_sha256")
        },
        "renderer": value["renderer"],
        "ollama": {"image": "ollama/ollama@sha256:" + "e" * 64, **value["ollama"]},
        "build": value["build"],
    }


class ComposeAnchorRunner(FakeRunner):
    def __init__(
        self,
        target,
        services: dict[str, list[str]],
        *,
        active_state: dict | None = None,
        release_schema: str = "usl-release/v2",
        release_payload: dict | None = None,
        healthy: bool = True,
        label_overrides: dict[str, dict[str, str]] | None = None,
    ) -> None:
        super().__init__(target)
        self.services = services
        self.active_state = active_state
        self.release_schema = release_schema
        self.release_payload = release_payload
        self.healthy = healthy
        self.label_overrides = label_overrides or {}

    def run(self, command: list[str], *, check: bool = True):
        self.commands.append(command)
        if command[:1] == ["cat"]:
            if self.active_state is not None and command[1] == self.active_state["release_manifest"]:
                return self.completed(
                    command,
                    json.dumps(self.release_payload or release_manifest(self.release_schema)),
                )
            if self.active_state is None:
                return subprocess.CompletedProcess(command, 1, "", "missing")
            return self.completed(command, json.dumps(self.active_state))
        if command[:2] == ["docker", "ps"]:
            service_filter = next(
                item for item in command
                if item.startswith("label=com.docker.compose.service=")
            )
            service = service_filter.rsplit("=", 1)[1]
            output = "".join(f"{identifier}\n" for identifier in self.services.get(service, []))
            return self.completed(command, output)
        if command[:2] == ["docker", "inspect"]:
            if command[-1] == "{{json .State}}":
                return self.completed(
                    command,
                    json.dumps({
                        "Running": self.healthy,
                        "Health": {"Status": "healthy" if self.healthy else "unhealthy"},
                    }),
                )
            identifier = command[2]
            service = next(
                name for name, identifiers in self.services.items()
                if identifier in identifiers
            )
            labels = {
                "com.docker.compose.project": self.target.project,
                "com.docker.compose.service": service,
                "com.docker.compose.project.config_files": "/release/compose.yaml,/release/production.yaml",
                "com.docker.compose.project.working_dir": "/release",
                "com.docker.compose.project.environment_file": "/runtime/site.env,/release/.env",
            }
            labels.update(self.label_overrides.get(identifier, {}))
            return self.completed(command, json.dumps(labels))
        if command[:3] == ["docker", "rm", "--force"]:
            identifier = command[3]
            for identifiers in self.services.values():
                if identifier in identifiers:
                    identifiers.remove(identifier)
            return self.completed(command, identifier + "\n")
        if command[:2] in (["test", "-f"], ["test", "-d"]):
            return self.completed(command, "")
        if command[:2] == ["readlink", "-f"]:
            return self.completed(command, command[-1] + "\n")
        if "compose" in command and command[-2:] == ["config", "--services"]:
            services = set(self.target.value["services"].values())
            services.discard(self.target.value["compose"]["anchor_service"])
            services.add("odoo")
            return self.completed(command, "".join(f"{service}\n" for service in sorted(services)))
        return super().run(command, check=check)


class RuntimeContractTests(unittest.TestCase):
    def test_local_staging_repositories_are_scoped_and_production_stays_remote(self):
        for targets in (TARGETS, HOST_TARGETS):
            staging = load_target("staging", targets)
            for key, repository in staging.value["backup"].items():
                self.assertEqual(repository, f"/var/lib/usl-odoo/restic/staging/{key.removesuffix('_repository')}")
            production = load_target("production", targets)
            self.assertTrue(all(value.startswith("s3:") for value in production.value["backup"].values()))
            for invalid in ("/tmp/restic", "/var/lib/usl-odoo/restic/staging/../production", "relative/repo"):
                value = copy.deepcopy(staging.value)
                value["backup"]["durable_repository"] = invalid
                with self.assertRaisesRegex(RuntimeError, "supported Restic repository"):
                    validate_target(value)
            production.value["backup"] = staging.value["backup"]
            with self.assertRaisesRegex(RuntimeError, "supported Restic repository"):
                validate_target(production.value)

    def test_mcp_readiness_binds_runtime_and_oauth_schema(self) -> None:
        value = {
            "schema": "usl-odoo-mcp-readiness/v1",
            "status": "ready",
            "server_version": "1.4.2",
            "targets": 1,
            "oauth": {"status": "ready", "schema_version": 1},
        }
        self.assertEqual(_validate_mcp_readiness(value, require_oauth=True)["oauth"]["status"], "ready")
        value["oauth"]["schema_version"] = 2
        with self.assertRaisesRegex(RuntimeError, "OAuth-vault schema"):
            _validate_mcp_readiness(value, require_oauth=True)

    def test_production_mcp_requires_ready_oauth(self) -> None:
        value = {
            "schema": "usl-odoo-mcp-readiness/v1",
            "status": "ready",
            "server_version": "1.0.0",
            "targets": 1,
            "oauth": {"status": "disabled", "schema_version": 1},
        }
        with self.assertRaisesRegex(RuntimeError, "OAuth vault"):
            _validate_mcp_readiness(value, require_oauth=True)
        self.assertEqual(_validate_mcp_readiness(value, require_oauth=False)["status"], "ready")

    def test_sign_readiness_binds_public_trust_and_dss_engine(self) -> None:
        value = {
            "schema": "usl-sign-readiness/v1",
            "status": "ready",
            "step_ca": {"status": "ok", "trust_sha256": "a" * 64},
            "dss": {"status": "ok", "engine_version": "6.4", "trust_sha256": "b" * 64},
        }
        self.assertEqual(_validate_sign_readiness(value), value)
        value["dss"]["trust_sha256"] = "unsafe"
        with self.assertRaisesRegex(RuntimeError, "trust identity"):
            _validate_sign_readiness(value)

    def test_all_versioned_targets_validate(self) -> None:
        for name in ("production", "staging", "local"):
            target = load_target(name, TARGETS)
            self.assertEqual(target.name, name)
            self.assertEqual(
                set(target.value["compose"]["profiles"]),
                {"document-renderer", "mcp", "paperless", "sign"},
            )
        self.assertEqual(
            load_target("production", TARGETS).value["compose"]["resource_overlay"],
            "compose.resources.production.json",
        )
        self.assertEqual(
            load_target("staging", TARGETS).value["compose"]["resource_overlay"],
            "compose.resources.staging.json",
        )
        self.assertEqual(
            load_target("production", TARGETS).value["ingress"],
            {
                "proxy_mode": True,
                "list_db": False,
                "dbfilter": "^odoo_production$",
                "websocket": True,
            },
        )
        self.assertTrue(load_target("staging", TARGETS).value["ingress"]["proxy_mode"])
        self.assertFalse(load_target("local", TARGETS).value["ingress"]["proxy_mode"])

    def test_secret_file_rejects_scope_fields(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "topology key"):
            validate_secret_text(
                "RESTIC_PASSWORD=secret\nCOMPOSE_PROJECT_NAME=foreign\n",
                ["RESTIC_PASSWORD"],
            )

    def test_target_rejects_overlapping_sign_secrets_and_evidence(self) -> None:
        target = load_target("local", TARGETS)
        value = json.loads(target.path.read_text(encoding="utf-8"))
        value["paths"]["sign_evidence"]["path"] = value["paths"]["sign_secrets"]["path"] + "/evidence"
        with self.assertRaisesRegex(RuntimeError, "must not overlap"):
            validate_target(value)

    def test_target_requires_a_tier_for_every_persistent_resource(self) -> None:
        target = load_target("local", TARGETS)
        value = json.loads(target.path.read_text(encoding="utf-8"))
        del value["volumes"]["odoo_postgres"]["tier"]
        with self.assertRaisesRegex(RuntimeError, "volumes.odoo_postgres fields differ"):
            validate_target(value)
        value = json.loads(target.path.read_text(encoding="utf-8"))
        value["paths"]["sign_secrets"]["tier"] = "foreign"
        with self.assertRaisesRegex(RuntimeError, "paths.sign_secrets.tier is invalid"):
            validate_target(value)

    def test_host_targets_split_bulk_and_database_tiers(self) -> None:
        for name in ("production", "staging"):
            target = load_target(name, TARGETS)
            tiers = target.value["storage"]["tiers"]
            self.assertEqual(tiers["bulk"]["path"], "/srv/storage")
            self.assertEqual(tiers["database"]["path"], "/srv/db")
            self.assertEqual(tiers["bulk"]["reserve_bytes"], 15 * 1024**3)
            self.assertEqual(tiers["database"]["reserve_bytes"], 2 * 1024**3)
            self.assertEqual(target.value["volumes"]["odoo_postgres"]["tier"], "database")
            self.assertEqual(target.value["volumes"]["mcp_oauth"]["tier"], "database")
            self.assertEqual(target.value["volumes"]["odoo_filestore"]["tier"], "bulk")

    def test_remote_targets_use_host_loopback_for_admission(self) -> None:
        expected = {
            "production": {
                "odoo": "http://127.0.0.1:18069",
                "odoo_websocket": "http://127.0.0.1:18072",
                "paperless": "http://127.0.0.1:18010",
                "mcp": "http://127.0.0.1:18000",
            },
            "staging": {
                "odoo": "http://127.0.0.1:19069",
                "odoo_websocket": "http://127.0.0.1:19072",
                "paperless": "http://127.0.0.1:19010",
                "mcp": "http://127.0.0.1:19000",
            },
        }
        for directory in (TARGETS, HOST_TARGETS):
            for name, endpoints in expected.items():
                with self.subTest(directory=directory.name, target=name):
                    target = load_target(name, directory)
                    self.assertEqual(target.value["admission_endpoints"], endpoints)

    def test_remote_target_rejects_public_admission_endpoint(self) -> None:
        target = load_target("staging", HOST_TARGETS)
        value = json.loads(target.path.read_text(encoding="utf-8"))
        value["admission_endpoints"]["odoo"] = value["endpoints"]["odoo"]
        with self.assertRaisesRegex(RuntimeError, "target-host loopback"):
            validate_target(value)

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
        self.assertEqual(command.count("--env-file"), 2)
        self.assertIn("/runtime/site.env", command)
        self.assertIn("/release/.env", command)
        for profile in target.value["compose"]["profiles"]:
            self.assertIn(profile, command)

    def test_staging_first_adoption_accepts_only_one_legacy_anchor(self) -> None:
        target = load_target("staging", HOST_TARGETS)
        runner = ComposeAnchorRunner(
            target,
            {"odoo": ["legacy-id"]},
            active_state=active_generation(target),
        )

        identity = compose_identity(target, runner)

        self.assertEqual(identity["container_id"], "legacy-id")
        self.assertEqual(identity["anchor_service"], "odoo")

    def test_staging_accepts_only_one_canonical_anchor(self) -> None:
        target = load_target("staging", HOST_TARGETS)
        runner = ComposeAnchorRunner(target, {"odoo-staging": ["canonical-id"]})

        identity = compose_identity(target, runner)

        self.assertEqual(identity["container_id"], "canonical-id")
        self.assertEqual(identity["anchor_service"], "odoo-staging")

    def test_staging_rejects_canonical_and_legacy_anchors_together(self) -> None:
        target = load_target("staging", HOST_TARGETS)
        runner = ComposeAnchorRunner(
            target,
            {"odoo-staging": ["canonical-id"], "odoo": ["legacy-id"]},
            active_state=active_generation(target),
        )

        with self.assertRaisesRegex(RuntimeError, "both canonical and legacy anchors"):
            compose_identity(target, runner)

    def test_staging_rejects_legacy_anchor_without_active_v2_release(self) -> None:
        target = load_target("staging", HOST_TARGETS)
        runner = ComposeAnchorRunner(
            target,
            {"odoo": ["legacy-id"]},
            active_state=active_generation(target),
            release_schema="usl-release/v3",
        )

        with self.assertRaisesRegex(RuntimeError, "not an active v2 release"):
            compose_identity(target, runner)

    def test_staging_rejects_legacy_anchor_without_active_state(self) -> None:
        target = load_target("staging", HOST_TARGETS)
        runner = ComposeAnchorRunner(target, {"odoo": ["legacy-id"]})

        with self.assertRaisesRegex(RuntimeError, "not an active v2 release"):
            compose_identity(target, runner)

    def test_staging_rejects_legacy_anchor_with_incomplete_release_manifest(self) -> None:
        target = load_target("staging", HOST_TARGETS)
        runner = ComposeAnchorRunner(
            target,
            {"odoo": ["legacy-id"]},
            active_state=active_generation(target),
            release_payload={"schema": "usl-release/v2"},
        )

        with self.assertRaisesRegex(RuntimeError, "active release manifest is invalid"):
            compose_identity(target, runner)

    def test_staging_rejects_legacy_anchor_bound_to_another_generation_manifest(self) -> None:
        target = load_target("staging", HOST_TARGETS)
        state = active_generation(target)
        state["release_manifest"] = (
            target.value["state_directory"]
            + "/generations/g20260901-stale/usl-release.json"
        )
        runner = ComposeAnchorRunner(target, {"odoo": ["legacy-id"]}, active_state=state)

        with self.assertRaisesRegex(RuntimeError, "does not match its generation"):
            compose_identity(target, runner)

    def test_staging_rejects_unhealthy_legacy_anchor(self) -> None:
        target = load_target("staging", HOST_TARGETS)
        runner = ComposeAnchorRunner(
            target,
            {"odoo": ["legacy-id"]},
            active_state=active_generation(target),
            healthy=False,
        )

        with self.assertRaisesRegex(RuntimeError, "not running and healthy"):
            compose_identity(target, runner)

    def test_staging_prefers_canonical_anchor_after_v3_activation(self) -> None:
        target = load_target("staging", HOST_TARGETS)
        runner = ComposeAnchorRunner(
            target,
            {"odoo-staging": ["canonical-id"], "odoo": ["legacy-id"]},
            active_state=active_generation(target),
            release_schema="usl-release/v3",
        )

        identity = compose_identity(target, runner)

        self.assertEqual(identity["container_id"], "canonical-id")
        self.assertEqual(identity["anchor_service"], "odoo-staging")

    def test_validation_failure_cleanup_makes_legacy_v2_adoption_retryable(self) -> None:
        target = load_target("staging", HOST_TARGETS)
        runner = ComposeAnchorRunner(
            target,
            {"odoo-staging": ["failed-id"], "odoo": ["legacy-id"]},
            active_state=active_generation(target),
        )
        legacy_identity = {"anchor_service": "odoo"}

        _cleanup_adoption_candidate_anchor(target, runner, legacy_identity)
        identity = compose_identity(target, runner)

        self.assertEqual(identity["container_id"], "legacy-id")
        self.assertEqual(runner.services["odoo-staging"], [])

    def test_staging_rejects_foreign_or_wrong_legacy_labels(self) -> None:
        target = load_target("staging", HOST_TARGETS)
        for labels, message in (
            ({"com.docker.compose.project": "foreign"}, "another Compose project"),
            ({"com.docker.compose.service": "wrong"}, "wrong Compose service label"),
        ):
            with self.subTest(message=message):
                runner = ComposeAnchorRunner(
                    target,
                    {"odoo": ["legacy-id"]},
                    active_state=active_generation(target),
                    label_overrides={"legacy-id": labels},
                )
                with self.assertRaisesRegex(RuntimeError, message):
                    compose_identity(target, runner)

    def test_production_does_not_probe_the_staging_legacy_transition(self) -> None:
        target = load_target("production", HOST_TARGETS)
        runner = ComposeAnchorRunner(target, {"odoo": ["production-id"]})

        identity = compose_identity(target, runner)

        self.assertEqual(identity["container_id"], "production-id")
        service_filters = [
            item
            for command in runner.commands
            for item in command
            if item.startswith("label=com.docker.compose.service=")
        ]
        self.assertEqual(service_filters, ["label=com.docker.compose.service=odoo"])

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
