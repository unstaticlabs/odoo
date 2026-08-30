from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import Mock, patch

from migration import manager
from migration.guard_runtime import guard
from migration.runtime import (
    CommandRunner,
    Completed,
    MODEL_MANIFEST_SHA256,
    RuntimeError,
    RuntimeStore,
    compose_files,
    inspect_project,
    read_secrets,
    resolve_ollama,
    runtime_environment,
    source_identity,
    verify_recorded_resources,
)


class FakeRunner(CommandRunner):
    def __init__(self, root: Path, project: str = "project-without-name-policy"):
        self.root = root
        self.project = project
        self.calls: list[tuple[str, ...]] = []
        self.container_state = "running"
        self.foreign_workdir: str | None = None
        self.has_resources = True
        self.documents_pending = 0

    def run(self, arguments, *, cwd=None, env=None, check=True):
        command = tuple(arguments)
        self.calls.append(command)
        if command[:3] == ("docker", "ps", "-aq"):
            return Completed(
                0,
                "container-1\ndb-1\n" if self.has_resources else "",
            )
        if command[:2] == ("docker", "inspect"):
            owner = self.foreign_workdir or str(self.root)
            return Completed(
                0,
                json.dumps(
                    [
                        {
                            "Id": "container-1",
                            "Name": "/runtime-odoo-1",
                            "Image": "sha256:image",
                            "Config": {
                                "Labels": {
                                    "com.docker.compose.project": self.project,
                                    "com.docker.compose.project.working_dir": owner,
                                    "com.docker.compose.service": "odoo",
                                    "org.opencontainers.image.revision": "b" * 40,
                                }
                            },
                            "State": {"Status": self.container_state},
                        },
                        {
                            "Id": "mcp-1",
                            "Name": "/runtime-odoo-mcp-1",
                            "Image": "sha256:mcp-image",
                            "Config": {
                                "Image": "usl-odoo-mcp@sha256:" + "a" * 64,
                                "Labels": {
                                    "com.docker.compose.project": self.project,
                                    "com.docker.compose.project.working_dir": owner,
                                    "com.docker.compose.service": "odoo-mcp",
                                    "org.opencontainers.image.revision": "2da51e7c596824d5226957777bbc1c70965ce9d4",
                                },
                            },
                            "State": {
                                "Status": self.container_state,
                                "Health": {"Status": "healthy"},
                            },
                        },
                        {
                            "Id": "db-1",
                            "Name": "/runtime-db-1",
                            "Image": "sha256:database",
                            "Config": {
                                "Labels": {
                                    "com.docker.compose.project": self.project,
                                    "com.docker.compose.project.working_dir": owner,
                                    "com.docker.compose.service": "db",
                                }
                            },
                            "State": {"Status": self.container_state},
                        },
                    ]
                ),
            )
        if command[:3] == ("docker", "exec", "db-1"):
            return Completed(
                0,
                json.dumps(
                    {
                        "active_operations": self.documents_pending,
                        "unresolved_operations": 0,
                        "approved_jobs": 4,
                        "configured_jobs": 4,
                        "backfill_complete": True,
                    }
                ),
            )
        if command[:4] == ("docker", "volume", "ls", "-q"):
            return Completed(0, "runtime-data\n" if self.has_resources else "")
        if command[:3] == ("docker", "volume", "inspect"):
            return Completed(
                0,
                json.dumps(
                    [
                        {
                            "Name": "runtime-data",
                            "Mountpoint": "/var/lib/docker/volumes/runtime-data",
                            "Labels": {"com.docker.compose.project": self.project},
                        }
                    ]
                ),
            )
        if command[:4] == ("docker", "network", "ls", "-q"):
            return Completed(0, "network-1\n" if self.has_resources else "")
        if command[:3] == ("docker", "network", "inspect"):
            return Completed(
                0,
                json.dumps(
                    [
                        {
                            "Id": "network-1",
                            "Name": "runtime-default",
                            "Labels": {"com.docker.compose.project": self.project},
                        }
                    ]
                ),
            )
        return Completed(0, "")


class MigrationManageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        (self.root / "private").mkdir(mode=0o700)
        self.source = self.root / "source"
        (self.source / "filestore").mkdir(parents=True)
        (self.source / "dump.sql").write_bytes(b"frozen dump")
        (self.source / "filestore/a").write_bytes(b"attachment")
        self.identity = self.root / "legacy.env"
        self.identity.write_text(
            "\n".join(
                (
                    "COMPOSE_PROJECT_NAME=project-without-name-policy",
                    "ODOO_INIT_DB=odoo_dev",
                    "POCKET_ID_CLIENT_SECRET=client-secret",
                    "POCKET_ID_ENCRYPTION_KEY=encryption-key",
                    "POCKET_ID_STATIC_API_KEY=api-key",
                    "POCKET_ID_VALENTIN_ID=valentin-id",
                )
            )
            + "\n",
            encoding="utf-8",
        )
        self.identity.chmod(0o600)
        (self.root / "personal-ai-keys.json").write_text("{}\n", encoding="utf-8")
        (self.root / "personal-ai-keys.json").chmod(0o600)
        self.runner = FakeRunner(self.root)
        self.original_root = manager.ROOT
        self.original_internal = manager.INTERNAL
        manager.ROOT = self.root
        manager.INTERNAL = self.root / "migration/internal"
        self.mcp = {
            "schema": "usl-odoo-mcp-release-v2",
            "repository": "https://github.com/unstaticlabs/odoo-mcp.git",
            "ref": "codex/odoo-mcp-vps-refactor",
            "commit": "2da51e7c596824d5226957777bbc1c70965ce9d4",
            "image": "usl-odoo-mcp@sha256:" + "a" * 64,
            "checkout": str(self.root / "odoo-mcp"),
        }
        self.mcp_release = patch.object(
            manager, "resolve_mcp_release", return_value=self.mcp
        )
        self.mcp_release.start()

    def tearDown(self):
        manager.ROOT = self.original_root
        manager.INTERNAL = self.original_internal
        self.mcp_release.stop()
        self.temporary.cleanup()

    def adopt_arguments(self):
        return Namespace(
            action="adopt",
            id="qa-current",
            project=self.runner.project,
            database="odoo_dev",
            source=self.source,
            source_sha256=hashlib.sha256(b"frozen dump").hexdigest(),
            identity_env=self.identity,
            personal_ai_key_file=self.root / "personal-ai-keys.json",
            profile="full",
            odoo_port=28669,
            gevent_port=28670,
            pocket_id_port=28671,
            paperless_port=28672,
            mcp_port=28673,
            odoo_url=None,
            pocket_id_url=None,
            paperless_url=None,
            mcp_url=None,
            mcp_repository=self.root / "odoo-mcp",
            ollama="container",
            ollama_models=None,
            image=[],
            release_commit=None,
            paperless_task_workers=3,
            embedding_batch_size=32,
        )

    def test_adoption_creates_private_resolved_state_and_status(self):
        with patch.object(manager, "git", return_value="a" * 40):
            runtime = manager.create_runtime(self.adopt_arguments(), self.runner, kind="qa")
            status = manager.check_runtime(runtime, self.runner)
        directory = self.root / "private/migration/runtimes/qa-current"
        self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((directory / "runtime.json").stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((directory / "secrets.env").stat().st_mode), 0o600)
        self.assertEqual(
            stat.S_IMODE((directory / "odoo-mcp-better-auth.secret").stat().st_mode),
            0o600,
        )
        self.assertEqual(
            stat.S_IMODE(
                (directory / "odoo-mcp-credential-encryption-key.secret").stat().st_mode
            ),
            0o600,
        )
        self.assertEqual(runtime["compose"]["project"], self.runner.project)
        self.assertEqual(runtime["release_commit"], "b" * 40)
        self.assertFalse(status["checkout_matches"])
        self.assertEqual(status["resources"], {"containers": 3, "volumes": 1, "networks": 1})
        self.assertTrue(status["documents"]["ready"])
        self.assertTrue(status["mcp"]["ready"])
        self.assertTrue(status["healthy"])

    def test_runtime_status_fails_health_when_documents_are_queued(self):
        self.runner.documents_pending = 1
        with patch.object(manager, "git", return_value="a" * 40):
            runtime = manager.create_runtime(
                self.adopt_arguments(),
                self.runner,
                kind="qa",
            )
            status = manager.check_runtime(runtime, self.runner)
        self.assertEqual(status["documents"]["active_operations"], 1)
        self.assertFalse(status["documents"]["ready"])
        self.assertFalse(status["healthy"])

    def test_secret_file_rejects_scope_fields(self):
        path = self.root / "secrets.env"
        path.write_text("COMPOSE_PROJECT_NAME=unsafe\n", encoding="utf-8")
        path.chmod(0o600)
        with self.assertRaisesRegex(RuntimeError, "scope fields"):
            read_secrets(path)

    def test_foreign_working_directory_fails_closed(self):
        self.runner.foreign_workdir = "/foreign/checkout"
        with self.assertRaisesRegex(RuntimeError, "foreign working-directory"):
            inspect_project(self.runner, self.runner.project, self.root)

    def test_exact_resource_sets_are_required_and_stop_preserves_volumes(self):
        recorded = inspect_project(self.runner, self.runner.project, self.root)
        changed = json.loads(json.dumps(recorded))
        changed["volumes"][0]["name"] = "other-volume"
        with self.assertRaisesRegex(RuntimeError, "exact resource set"):
            verify_recorded_resources(recorded, changed)
        manager.stop_runtime({"compose": {"project": self.runner.project}, "resources": recorded}, self.runner)
        self.assertIn(("docker", "stop", "container-1", "db-1", "mcp-1"), self.runner.calls)
        self.assertFalse(any(call[:3] == ("docker", "volume", "rm") for call in self.runner.calls))

    def test_transition_protection_does_not_depend_on_project_name(self):
        runtime = {
            "schema": "usl-migration-runtime-v1",
            "id": "qa-current",
            "kind": "qa",
            "status": "transition-live",
            "private_directory": str(self.root / "private/migration/runtimes/qa-current"),
            "compose": {
                "project": "project-without-name-policy",
                "working_directory": str(self.root),
            },
        }
        store = RuntimeStore(self.root)
        store.create(runtime, {})
        args = Namespace(action="refresh", runtime="qa-current", fresh=True, confirm="REFRESH:qa-current")
        with self.assertRaisesRegex(RuntimeError, "protected runtime"):
            manager.command_qa(args, self.runner)
        with self.assertRaisesRegex(SystemExit, "recorded as transition-live"):
            guard(self.root, "project-without-name-policy", "test mutation")

    def test_runtime_environment_overrides_ambient_scope_and_disables_live_integrations(self):
        runtime = {
            "id": "qa-current",
            "database": "odoo_dev",
            "private_directory": str(self.root / "private/runtime"),
            "ports": {"odoo": 1, "gevent": 2, "paperless": 3, "pocket_id": 4, "mcp": 5},
            "urls": {
                "odoo": "http://odoo",
                "paperless": "http://paperless",
                "pocket_id": "http://id",
                "mcp": "http://mcp.localhost:5",
            },
            "source": {"path": str(self.source), "dump_sha256": "e" * 64},
            "personal_ai_key_file": str(self.root / "personal-ai-keys.json"),
            "ollama": {"mode": "container"},
            "mcp": self.mcp,
            "compose": {
                "project": "recorded-project",
                "files": [str(self.root / "compose.yaml")],
                "profiles": ["paperless", "mcp"],
            },
        }
        with patch.dict(os.environ, {"COMPOSE_PROJECT_NAME": "ambient-project", "USL_EINVOICE_LIVE_ENABLED": "1"}):
            environment = runtime_environment(
                runtime,
                {
                    "POCKET_ID_PROSPER_EMAIL": "prosper@identity.test",
                    "POCKET_ID_PROSPER_ODOO_EMAIL": "",
                },
            )
        self.assertEqual(environment["COMPOSE_PROJECT_NAME"], "recorded-project")
        self.assertEqual(environment["ODOO_DB_NAME"], "odoo_dev")
        self.assertEqual(environment["ODOO_DB_FILTER"], "^odoo_dev$")
        self.assertEqual(environment["POCKET_ID_PROSPER_ODOO_EMAIL"], "")
        self.assertEqual(environment["USL_EINVOICE_LIVE_ENABLED"], "0")
        self.assertEqual(environment["USL_EREPORTING_LIVE_ENABLED"], "0")
        self.assertEqual(environment["ODOO_MCP_HTTP_PORT"], "5")
        self.assertEqual(environment["ODOO_MCP_IMAGE"], self.mcp["image"])
        self.assertEqual(environment["ODOO_MCP_RELEASE_COMMIT"], self.mcp["commit"])
        self.assertEqual(environment["ODOO_MCP_ALLOW_LOCAL_HTTP_ODOO"], "false")
        self.assertEqual(environment["COMPOSE_PROFILES"], "paperless,mcp")

    def test_runtime_environment_does_not_reuse_adopted_local_image_ids(self):
        runtime = {
            "id": "qa-current",
            "database": "odoo_dev",
            "private_directory": str(self.root / "private/runtime"),
            "ports": {"odoo": 1, "gevent": 2, "paperless": 3, "pocket_id": 4, "mcp": 5},
            "urls": {"odoo": "http://odoo", "paperless": "http://paperless", "pocket_id": "http://id", "mcp": "http://mcp.localhost:5"},
            "source": {"path": str(self.source), "dump_sha256": "e" * 64},
            "personal_ai_key_file": str(self.root / "personal-ai-keys.json"),
            "ollama": {"mode": "container"},
            "mcp": self.mcp,
            "compose": {"project": "recorded-project", "files": [str(self.root / "compose.yaml")], "profiles": ["mcp"]},
            "images": {
                "odoo": "sha256:" + "1" * 64,
                "paperless-webserver": "sha256:" + "2" * 64,
            },
        }
        environment = runtime_environment(runtime, {})
        self.assertNotIn("ODOO_IMAGE", environment)
        self.assertNotIn("PAPERLESS_IMAGE", environment)

        runtime["images"] = {
            "odoo": "registry.example/odoo@sha256:" + "3" * 64,
            "paperless-webserver": "registry.example/paperless@sha256:" + "4" * 64,
        }
        environment = runtime_environment(runtime, {})
        self.assertEqual(environment["ODOO_IMAGE"], runtime["images"]["odoo"])
        self.assertEqual(
            environment["PAPERLESS_IMAGE"], runtime["images"]["paperless-webserver"]
        )

    def test_runtime_environment_supports_checkpointing_a_pre_mcp_runtime(self):
        runtime = {
            "id": "transition-legacy",
            "database": "odoo_dev",
            "private_directory": str(self.root / "private/runtime"),
            "ports": {"odoo": 1, "gevent": 2, "paperless": 3, "pocket_id": 4},
            "urls": {
                "odoo": "http://odoo.localhost:1",
                "paperless": "http://paperless.localhost:3",
                "pocket_id": "http://id.localhost:4",
            },
            "source": {"path": str(self.source), "dump_sha256": "e" * 64},
            "personal_ai_key_file": str(self.root / "personal-ai-keys.json"),
            "ollama": {"mode": "container"},
            "compose": {
                "project": "recorded-project",
                "working_directory": str(self.root),
                "files": [str(self.root / "compose.yaml")],
                "profiles": ["paperless"],
            },
        }

        environment = runtime_environment(runtime, {})

        self.assertEqual(environment["ODOO_HTTP_PORT"], "1")
        self.assertNotIn("ODOO_MCP_HTTP_PORT", environment)
        self.assertNotIn("ODOO_MCP_IMAGE", environment)
        self.assertNotIn("USL_DOCUMENTS_MCP_REPOSITORY", environment)

    def test_runtime_environment_rejects_partial_mcp_identity(self):
        runtime = {
            "id": "transition-partial-mcp",
            "database": "odoo_dev",
            "private_directory": str(self.root / "private/runtime"),
            "ports": {
                "odoo": 1,
                "gevent": 2,
                "paperless": 3,
                "pocket_id": 4,
                "mcp": 5,
            },
            "urls": {
                "odoo": "http://odoo.localhost:1",
                "paperless": "http://paperless.localhost:3",
                "pocket_id": "http://id.localhost:4",
            },
            "source": {"path": str(self.source), "dump_sha256": "e" * 64},
            "personal_ai_key_file": str(self.root / "personal-ai-keys.json"),
            "ollama": {"mode": "container"},
            "compose": {
                "project": "recorded-project",
                "working_directory": str(self.root),
                "files": [str(self.root / "compose.yaml")],
                "profiles": ["paperless", "mcp"],
            },
        }

        with self.assertRaisesRegex(RuntimeError, "incomplete Odoo MCP identity"):
            runtime_environment(runtime, {})

    def test_local_production_override_reaches_the_pocket_id_runtime(self):
        runtime = {
            "id": "transition-current",
            "database": "odoo_dev",
            "ports": {"odoo": 28669, "gevent": 28670, "paperless": 28672, "pocket_id": 28671, "mcp": 28673},
            "urls": {
                "odoo": "http://odoo.localhost:28669",
                "paperless": "http://paperless.localhost:28672",
                "pocket_id": "http://pocket-id.localhost:28671",
                "mcp": "http://mcp.localhost:28673",
            },
            "source": {"path": str(self.source), "dump_sha256": "a" * 64},
            "private_directory": str(self.root / "private/migration/runtimes/transition-current"),
            "personal_ai_key_file": str(self.root / "personal-ai-keys.json"),
            "ollama": {
                "mode": "container",
                "model": "model",
                "manifest_sha256": "b" * 64,
            },
            "mcp": self.mcp,
            "compose": {
                "project": "fixed-runtime",
                "working_directory": str(self.root),
                "files": [str(self.root / "compose.yaml"), str(self.root / "compose.production.yaml")],
                "profiles": ["mcp"],
            },
        }
        environment = runtime_environment(runtime, {})
        self.assertEqual(
            environment["USL_POCKET_ID_COMPOSE_EXTRA_FILE"],
            str(self.root / "compose.production.yaml"),
        )

    def test_resolved_compose_environment_has_one_stable_private_path(self):
        runtime = {
            "id": "qa-current",
            "database": "odoo_dev",
            "private_directory": str(self.root / "private/runtime"),
            "ports": {"odoo": 1, "gevent": 2, "paperless": 3, "pocket_id": 4, "mcp": 5},
            "urls": {
                "odoo": "http://odoo",
                "paperless": "http://paperless",
                "pocket_id": "http://id",
                "mcp": "http://mcp.localhost:5",
            },
            "source": {"path": str(self.source), "dump_sha256": "e" * 64},
            "personal_ai_key_file": str(self.root / "personal-ai-keys.json"),
            "ollama": {"mode": "container"},
            "mcp": self.mcp,
            "compose": {
                "project": "recorded-project",
                "files": [str(self.root / "compose.yaml")],
                "profiles": ["mcp"],
            },
        }

        first_path, _environment = manager.combined_env_file(
            runtime, {"POCKET_ID_CLIENT_SECRET": "first"}
        )
        second_path, _environment = manager.combined_env_file(
            runtime, {"POCKET_ID_CLIENT_SECRET": "second"}
        )

        self.assertEqual(first_path, second_path)
        self.assertEqual(first_path.name, "resolved-compose.env")
        self.assertEqual(stat.S_IMODE(first_path.stat().st_mode), 0o600)
        self.assertNotIn("first", first_path.read_text(encoding="utf-8"))
        self.assertIn(
            "POCKET_ID_CLIENT_SECRET=second\n",
            first_path.read_text(encoding="utf-8"),
        )

    def test_failed_refresh_recovers_exact_partial_resource_set(self):
        runtime = {
            "id": "qa-current",
            "status": "failed",
            "compose": {"project": self.runner.project},
            "resources": {"containers": [], "volumes": [], "networks": []},
        }
        store = Mock()
        manager.recover_failed_runtime_resources(runtime, store, self.runner)
        self.assertEqual(len(runtime["resources"]["containers"]), 3)
        store.save.assert_called_once_with(runtime)

    def test_failed_transition_retry_recovers_resources_before_destroy(self):
        runtime = {
            "schema": "usl-migration-runtime-v1",
            "id": "transition-current",
            "kind": "transition",
            "status": "failed",
            "release_commit": "b" * 40,
            "private_directory": str(
                self.root / "private/migration/runtimes/transition-current"
            ),
            "compose": {
                "project": self.runner.project,
                "working_directory": str(self.root),
            },
            "resources": {"containers": [], "volumes": [], "networks": []},
        }
        RuntimeStore(self.root).create(runtime, {})
        arguments = Namespace(
            action="reconstruct",
            runtime="transition-current",
            confirm="RECONSTRUCT:transition-current",
            image=["odoo=usl-odoo-transition:current"],
        )
        with (
            patch.object(manager, "ensure_clean_checkout", return_value="b" * 40),
            patch.object(manager, "run_internal"),
            patch.object(manager, "start_mcp_runtime"),
        ):
            result = manager.command_transition(arguments, self.runner)

        self.assertEqual(result["status"], "reconstructed")
        self.assertEqual(
            RuntimeStore(self.root).load("transition-current")["images"]["odoo"],
            "usl-odoo-transition:current",
        )
        self.assertIn(("docker", "rm", "--force", "container-1", "db-1", "mcp-1"), self.runner.calls)
        self.assertIn(("docker", "volume", "rm", "runtime-data"), self.runner.calls)

    def test_failed_post_boundary_transition_resumes_only_finalization(self):
        runtime_id = "transition-current"
        runtime = {
            "schema": "usl-migration-runtime-v1",
            "id": runtime_id,
            "kind": "transition",
            "status": "failed",
            "release_commit": "b" * 40,
            "source": {"dump_sha256": "e" * 64},
            "private_directory": str(
                self.root / "private/migration/runtimes" / runtime_id
            ),
            "compose": {
                "project": self.runner.project,
                "working_directory": str(self.root),
            },
            "resources": inspect_project(self.runner, self.runner.project, self.root),
            "images": {"odoo": "old-image"},
            "mcp": self.mcp,
        }
        RuntimeStore(self.root).create(runtime, {})
        run_directory = self.root / "private/migration/runs"
        run_directory.mkdir(parents=True)
        report = run_directory / f"{runtime_id}-20260830T150000Z.json"
        report.write_text(
            json.dumps(
                {
                    "schema": "usl-production-migration-run-v2",
                    "outcome": "failed",
                    "purpose": "production",
                    "source_dump_sha256": "e" * 64,
                    "compose_project": self.runner.project,
                    "stages": [
                        {"name": "finalize migration boundary", "status": 0},
                        {"name": "apply target configuration", "status": 1},
                    ],
                }
            ),
            encoding="utf-8",
        )
        report.chmod(0o600)
        arguments = Namespace(
            action="resume-finalization",
            runtime=runtime_id,
            confirm=f"RESUME-FINALIZATION:{runtime_id}",
            image=["odoo=current-image"],
        )
        with (
            patch.object(manager, "ensure_clean_checkout", return_value="c" * 40),
            patch.object(manager, "run_internal") as internal,
            patch.object(manager, "start_mcp_runtime"),
        ):
            result = manager.command_transition(arguments, self.runner)

        self.assertEqual(result["status"], "reconstructed")
        self.assertEqual(
            internal.call_args.args[3],
            [str(self.root / "migration/internal/reconstruct"), "transition-finalize"],
        )
        saved = RuntimeStore(self.root).load(runtime_id)
        self.assertEqual(saved["release_commit"], "c" * 40)
        self.assertEqual(saved["images"]["odoo"], "current-image")
        self.assertEqual(saved["images"]["odoo-mcp"], self.mcp["image"])
        self.assertEqual(saved["finalization_resume_evidence"], str(report))

    def test_finalization_rebinds_mcp_only_before_its_container_exists(self):
        previous = dict(self.mcp)
        previous.update(
            {
                "commit": "9a0e681a1e3ca82400e6c8033f251ccc318be44e",
                "image": "usl-odoo-mcp@sha256:" + "b" * 64,
            }
        )
        runtime = {"mcp": previous, "images": {"odoo-mcp": previous["image"]}}
        resources = {"containers": [], "volumes": [], "networks": []}

        self.assertTrue(manager.rebind_mcp_release(runtime, resources))
        self.assertEqual(runtime["mcp"]["ref"], "codex/odoo-mcp-vps-refactor")
        self.assertEqual(runtime["images"]["odoo-mcp"], self.mcp["image"])
        self.assertEqual(
            runtime["mcp_release_rebound"]["to_commit"], self.mcp["commit"]
        )

        runtime = {"mcp": previous, "images": {"odoo-mcp": previous["image"]}}
        resources["containers"] = [{"service": "odoo-mcp", "state": "exited"}]
        with self.assertRaisesRegex(RuntimeError, "earlier MCP container exists"):
            manager.rebind_mcp_release(runtime, resources)

    def test_runtime_image_assignments_reject_mcp_release_drift(self):
        runtime = {"mcp": self.mcp, "images": {}}
        with self.assertRaisesRegex(RuntimeError, "differs from the pinned release"):
            manager.apply_runtime_image_assignments(
                runtime, ["odoo-mcp=usl-odoo-mcp:unqualified"]
            )

    def test_native_macos_ollama_is_preferred_and_linux_uses_container(self):
        models = self.root / "models"
        manifest = models / "manifests/registry.ollama.ai/library/usl-bge-m3/documents-20260824-rc1"
        manifest.parent.mkdir(parents=True)
        manifest.write_text("manifest", encoding="utf-8")
        with patch("migration.runtime.sha256_file", return_value=MODEL_MANIFEST_SHA256):
            native = resolve_ollama(
                "auto", system="Darwin", executable="/usr/local/bin/ollama", models=models, reachable=True
            )
        linux = resolve_ollama("auto", system="Linux", executable=None)
        self.assertEqual(native["mode"], "native")
        self.assertEqual(linux["mode"], "container")
        self.assertIn("compose.ollama-native.yaml", compose_files(self.root, "qa", "native")[-1])
        self.assertFalse(
            any("compose.production.yaml" in item for item in compose_files(self.root, "qa", "native"))
        )
        self.assertTrue(
            any(
                "compose.production.yaml" in item
                for item in compose_files(self.root, "transition", "native")
            )
        )
        self.assertFalse(any("ollama-native" in item for item in compose_files(self.root, "production", "container")))

    def test_installed_but_unreachable_native_ollama_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, "fallback is forbidden"):
            resolve_ollama(
                "auto", system="Darwin", executable="/usr/local/bin/ollama", reachable=False
            )

    def test_login_link_ttl_is_limited_to_eight_hours(self):
        self.assertEqual(manager.ttl_minutes("8h"), 480)
        with self.assertRaisesRegex(RuntimeError, "may not exceed"):
            manager.ttl_minutes("9h")

    def test_source_checksum_mismatch_fails_before_runtime_use(self):
        with self.assertRaisesRegex(RuntimeError, "checksum"):
            source_identity(self.source, "0" * 64)

    def test_new_transition_definition_has_no_project_name_convention(self):
        self.runner.has_resources = False
        secrets = self.root / "transition-secrets.env"
        secrets.write_text(
            "\n".join(
                (
                    "POCKET_ID_CLIENT_SECRET=client-secret",
                    "POCKET_ID_ENCRYPTION_KEY=encryption-key",
                    "POCKET_ID_STATIC_API_KEY=api-key",
                    "POCKET_ID_VALENTIN_ID=valentin-id",
                )
            )
            + "\n",
            encoding="utf-8",
        )
        secrets.chmod(0o600)
        args = self.adopt_arguments()
        args.id = "transition-current"
        args.project = "fixed-runtime"
        self.runner.project = args.project
        args.identity_env = None
        args.secrets_file = secrets
        with patch.object(
            manager,
            "git",
            side_effect=lambda *arguments: "" if arguments[0] == "status" else "c" * 40,
        ):
            runtime = manager.create_runtime(args, self.runner, kind="transition", adopt=False)
        self.assertEqual(runtime["status"], "defined")
        self.assertFalse(runtime["adopted"])
        self.assertEqual(runtime["resources"], {"containers": [], "volumes": [], "networks": []})

    def test_mark_live_applies_transition_policy_before_protection(self):
        self.runner.project = "fixed-runtime"
        runtime = {
            "schema": "usl-migration-runtime-v1",
            "id": "transition-current",
            "kind": "transition",
            "status": "reconstructed",
            "private_directory": str(
                self.root / "private/migration/runtimes/transition-current"
            ),
            "compose": {
                "project": "fixed-runtime",
                "working_directory": str(self.root),
            },
        }
        RuntimeStore(self.root).create(
            runtime,
            {
                "POCKET_ID_CLIENT_SECRET": "client-secret",
                "POCKET_ID_ENCRYPTION_KEY": "encryption-key",
                "POCKET_ID_STATIC_API_KEY": "api-key",
                "POCKET_ID_VALENTIN_ID": "valentin-id",
            },
        )
        arguments = Namespace(
            action="mark-live",
            runtime="transition-current",
            confirm="MARK-LIVE:transition-current",
        )
        with patch.object(manager, "run_internal") as internal:
            result = manager.command_transition(arguments, self.runner)
        self.assertEqual(result["status"], "transition-live")
        self.assertEqual(
            internal.call_args.args[3],
            [str(self.root / "migration/internal/transition-activate")],
        )
        self.assertEqual(
            internal.call_args.kwargs["extra_environment"],
            {"USL_MIGRATION_PURPOSE": "transition"},
        )
        self.assertEqual(
            RuntimeStore(self.root).load("transition-current")["status"],
            "transition-live",
        )
        stored = RuntimeStore(self.root).load("transition-current")
        self.assertEqual(
            [item["id"] for item in stored["resources"]["containers"]],
            ["container-1", "db-1", "mcp-1"],
        )

    def test_transition_checkpoint_uses_private_runtime_and_records_identity(self):
        runtime = {
            "schema": "usl-migration-runtime-v1",
            "id": "transition-current",
            "kind": "transition",
            "status": "reconstructed",
            "release_commit": "b" * 40,
            "private_directory": str(
                self.root / "private/migration/runtimes/transition-current"
            ),
            "compose": {"project": "fixed-runtime", "working_directory": str(self.root)},
            "ollama": {"mode": "container"},
        }
        RuntimeStore(self.root).create(
            runtime,
            {
                "POCKET_ID_CLIENT_SECRET": "client-secret",
                "POCKET_ID_ENCRYPTION_KEY": "encryption-key",
                "POCKET_ID_STATIC_API_KEY": "api-key",
                "POCKET_ID_VALENTIN_ID": "valentin-id",
            },
        )
        arguments = Namespace(
            action="checkpoint",
            runtime="transition-current",
            label="pre-upgrade",
        )
        with (
            patch.object(manager, "now", return_value="2026-08-30T09:10:11+00:00"),
            patch.object(manager, "run_internal") as internal,
        ):
            result = manager.command_transition(arguments, self.runner)
        self.assertEqual(result["checkpoint"], "20260830T091011Z-pre-upgrade")
        self.assertEqual(
            internal.call_args.args[3],
            [
                sys.executable,
                "-m",
                "migration.transition_checkpoint",
                "create",
                "20260830T091011Z-pre-upgrade",
            ],
        )
        saved = RuntimeStore(self.root).load("transition-current")
        self.assertEqual(saved["last_checkpoint"]["status"], "verified")

    def test_transition_retirement_requires_and_preserves_verified_checkpoint(self):
        runtime_id = "transition-current"
        release_commit = "b" * 40
        runtime_directory = self.root / "private/migration/runtimes" / runtime_id
        resources = inspect_project(self.runner, self.runner.project, self.root)
        runtime = {
            "schema": "usl-migration-runtime-v1",
            "id": runtime_id,
            "kind": "transition",
            "status": "transition-live",
            "release_commit": release_commit,
            "private_directory": str(runtime_directory),
            "compose": {
                "project": self.runner.project,
                "working_directory": str(self.root),
            },
            "resources": resources,
        }
        store = RuntimeStore(self.root)
        store.create(runtime, {})
        arguments = Namespace(
            action="retire",
            runtime=runtime_id,
            confirm=f"RETIRE:{runtime_id}",
        )
        with self.assertRaisesRegex(RuntimeError, "requires a verified checkpoint"):
            manager.command_transition(arguments, self.runner)

        checkpoint_id = "20260830T091011Z-before-rebuild"
        runtime["last_checkpoint"] = {
            "id": checkpoint_id,
            "release_commit": release_commit,
            "status": "verified",
        }
        store.save(runtime)
        checkpoint_directory = runtime_directory / "checkpoints" / checkpoint_id
        checkpoint_directory.mkdir(parents=True)
        manifest = checkpoint_directory / "manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "schema": "usl-transition-checkpoint/v1",
                    "id": checkpoint_id,
                    "runtime_id": runtime_id,
                    "project": self.runner.project,
                    "release_commit": release_commit,
                    "status": "verified",
                }
            ),
            encoding="utf-8",
        )
        manifest.chmod(0o600)

        result = manager.command_transition(arguments, self.runner)

        self.assertEqual(result["status"], "retired")
        self.assertEqual(result["checkpoint"], checkpoint_id)
        saved = store.load(runtime_id)
        self.assertEqual(saved["resources"], {"containers": [], "volumes": [], "networks": []})
        self.assertEqual(saved["retired_checkpoint"]["id"], checkpoint_id)
        self.assertTrue(manifest.is_file())
        self.assertIn(("docker", "rm", "--force", "container-1", "db-1", "mcp-1"), self.runner.calls)
        self.assertIn(("docker", "volume", "rm", "runtime-data"), self.runner.calls)

    def test_transition_start_and_stop_touch_only_operational_services(self):
        runtime = {
            "schema": "usl-migration-runtime-v1",
            "id": "transition-current",
            "kind": "transition",
            "status": "transition-live",
            "private_directory": str(
                self.root / "private/migration/runtimes/transition-current"
            ),
            "compose": {"project": self.runner.project, "working_directory": str(self.root)},
            "resources": {
                "containers": [
                    {"id": "container-1", "service": "odoo", "state": "running"},
                    {"id": "db-1", "service": "db", "state": "running"},
                    {"id": "mcp-1", "service": "odoo-mcp", "state": "running"},
                ],
                "volumes": [{"name": "runtime-data"}],
                "networks": [{"id": "network-1", "name": "runtime-default"}],
            },
        }
        RuntimeStore(self.root).create(
            runtime,
            {
                "POCKET_ID_CLIENT_SECRET": "client-secret",
                "POCKET_ID_ENCRYPTION_KEY": "encryption-key",
                "POCKET_ID_STATIC_API_KEY": "api-key",
                "POCKET_ID_VALENTIN_ID": "valentin-id",
            },
        )
        result = manager.command_transition(
            Namespace(action="stop", runtime="transition-current"), self.runner
        )
        self.assertTrue(result["data_preserved"])
        self.assertIn(("docker", "stop", "container-1", "db-1", "mcp-1"), self.runner.calls)

    def test_candidate_arguments_are_ordered_by_the_public_interface(self):
        with patch.object(manager, "git", return_value="b" * 40):
            runtime = manager.create_runtime(self.adopt_arguments(), self.runner, kind="qa")
        arguments = Namespace(
            domain="candidate",
            action="verify",
            runtime=runtime["id"],
            source_dir=None,
            candidate_dir=self.root / "candidate",
            fingerprint="d" * 64,
        )
        with (
            patch.object(
                manager,
                "git",
                side_effect=lambda *arguments: "" if arguments[0] == "status" else "b" * 40,
            ),
            patch.object(manager, "run_internal") as internal,
        ):
            manager.command_release_domain(arguments, self.runner)
        command = internal.call_args.args[3]
        self.assertEqual(command[1:4], ["verify", str(arguments.candidate_dir), "d" * 64])
        self.assertEqual(command[4], str(self.source))

    @unittest.skipUnless(shutil.which("docker"), "Docker Compose CLI is unavailable")
    def test_native_and_linux_compose_topologies_render_distinct_ollama_services(self):
        repository = Path(__file__).resolve().parents[2]
        environment = {
            **os.environ,
            "POCKET_ID_CLIENT_SECRET": "dummy-client",
            "POCKET_ID_ENCRYPTION_KEY": "dummy-encryption",
            "POCKET_ID_STATIC_API_KEY": "dummy-api",
            "USL_PERSONAL_AI_MASTER_KEYS_HOST_PATH": "/tmp/dummy",
        }
        native = subprocess.run(
            (
                "docker", "compose", "-p", "usl-render-native-test",
                "-f", "compose.yaml", "-f", "compose.pocket-id.yaml",
                "-f", "compose.ollama-native.yaml", "--profile", "paperless",
                "--profile", "mcp",
                "config", "--services",
            ),
            cwd=repository,
            env=environment,
            text=True,
            capture_output=True,
            check=True,
        ).stdout.splitlines()
        production_environment = {
            **environment,
            "ODOO_HTTP_PORT": "18069",
            "ODOO_GEVENT_PORT": "18072",
            "PAPERLESS_HTTP_PORT": "18010",
            "ODOO_PUBLIC_BASE_URL": "https://odoo.example.test",
            "PAPERLESS_PUBLIC_URL": "https://paperless.example.test",
            "POCKET_ID_APP_URL": "https://id.example.test",
            "ODOO_MCP_PUBLIC_ORIGIN": "https://mcp.example.test",
            "ODOO_MCP_ALLOW_LOCAL_HTTP_ODOO": "false",
            "POCKET_ID_CLIENT_ID": "odoo-client",
            "POCKET_ID_GROUP_NAME": "odoo-users",
            "POCKET_ID_PAPERLESS_CLIENT_ID": "paperless-client",
            "POCKET_ID_PAPERLESS_CLIENT_SECRET": "dummy-paperless",
            "PAPERLESS_SSO_BASE_GROUP": "documents-users",
            "USL_EXTERNAL_IDENTITY_NETWORK": "identity-net",
            "USL_EXTERNAL_INGRESS_NETWORK": "ingress-net",
        }
        for name in (
            "USL_ODOO_POSTGRES_VOLUME", "USL_ODOO_DATA_VOLUME",
            "USL_PAPERLESS_POSTGRES_VOLUME", "USL_PAPERLESS_BROKER_VOLUME",
            "USL_PAPERLESS_DATA_VOLUME", "USL_PAPERLESS_MEDIA_VOLUME",
            "USL_PAPERLESS_EXPORT_VOLUME", "USL_PAPERLESS_CONSUME_VOLUME",
            "USL_PAPERLESS_TRASH_VOLUME",
            "USL_ODOO_MCP_OAUTH_VOLUME",
        ):
            production_environment[name] = name.lower().replace("_", "-")
        linux = subprocess.run(
            (
                "docker", "compose", "-p", "usl-render-linux-test",
                "-f", "compose.yaml", "-f", "compose.production.yaml",
                "-f", "compose.external-pocket-id.yaml", "--profile", "paperless",
                "--profile", "mcp",
                "config", "--services",
            ),
            cwd=repository,
            env=production_environment,
            text=True,
            capture_output=True,
            check=True,
        ).stdout.splitlines()
        self.assertNotIn("paperless-ollama", native)
        self.assertNotIn("paperless-model-init", native)
        self.assertIn("paperless-model-preflight", native)
        self.assertIn("odoo-mcp", native)
        self.assertIn("odoo-mcp-oauth-init", native)
        self.assertIn("paperless-ollama", linux)
        self.assertIn("paperless-model-init", linux)
        self.assertIn("odoo-mcp", linux)


if __name__ == "__main__":
    unittest.main()
