from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WRAPPER = ROOT / "scripts" / "migration-legacy"
LEGACY_SERVICES = {
    "accounting-source-db",
    "accounting-migration",
    "project-migration",
    "b2c-migration",
    "tese-migration",
    "platform-billing-migration",
}
MIGRATION_TARGET_MARKERS = (
    "/mnt/accounting-source",
    "/mnt/accounting-migration-addons",
    "/mnt/identity-migration-addons",
    "/mnt/product-migration-addons",
    "/mnt/b2c-migration-addons",
    "/mnt/hr-migration-addons",
    "/mnt/project-migration-addons",
    "/mnt/tese-migration-addons",
    "/mnt/platform-billing-migration-addons",
    "/mnt/collaboration-migration-addons",
)


def run(*arguments: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        arguments,
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )


def render(*arguments: str) -> dict:
    environment = os.environ.copy()
    environment.update(
        {
            "POCKET_ID_CLIENT_ID": "migration-boundary-odoo-client",
            "POCKET_ID_CLIENT_SECRET": "migration-boundary-odoo-secret",
            "POCKET_ID_ENCRYPTION_KEY": "migration-boundary-encryption-key",
            "POCKET_ID_STATIC_API_KEY": "migration-boundary-static-api-key",
            "POCKET_ID_APP_URL": "http://pocket-id.localhost:1411",
            "POCKET_ID_HTTP_PORT": "1411",
            "ODOO_HTTP_PORT": "8069",
            "ODOO_GEVENT_PORT": "8072",
            "PAPERLESS_HTTP_PORT": "8010",
        },
    )
    result = run(
        "docker",
        "compose",
        *arguments,
        "config",
        "--format",
        "json",
        env=environment,
    )
    if result.returncode:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout)


class MigrationLegacyBoundaryTests(unittest.TestCase):
    def assert_product_render(self, value: dict) -> None:
        services = value["services"]
        self.assertFalse(LEGACY_SERVICES.intersection(services))
        serialized = json.dumps(services, sort_keys=True)
        for marker in MIGRATION_TARGET_MARKERS:
            self.assertNotIn(marker, serialized)

    def test_normal_development_test_qa_and_production_renders_are_product_only(self) -> None:
        self.assert_product_render(render())
        self.assert_product_render(render("--profile", "init"))
        self.assert_product_render(render("--profile", "test"))
        self.assert_product_render(render("--profile", "devcontainer"))
        self.assert_product_render(
            render(
                "-f",
                "compose.yaml",
                "-f",
                "compose.pocket-id.yaml",
                "--profile",
                "test",
            ),
        )
        self.assert_product_render(
            render(
                "-f",
                "compose.yaml",
                "-f",
                "compose.preprod.yaml",
                "--profile",
                "init",
            ),
        )

    def test_explicit_legacy_overlay_recovers_source_and_reconstruction_services(self) -> None:
        environment = os.environ.copy()
        environment.update(
            {
                "COMPOSE_PROJECT_NAME": "usl-odoo-migration-legacy-test",
                "USL_ONLINE_DUMP_DIR": "/private/tmp/usl-frozen-source-test",
            },
        )
        result = run(
            "docker",
            "compose",
            "-f",
            "compose.yaml",
            "-f",
            "compose.migration-legacy.yaml",
            "--profile",
            "migration-legacy",
            "config",
            "--format",
            "json",
            env=environment,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        services = json.loads(result.stdout)["services"]
        self.assertTrue(LEGACY_SERVICES.issubset(services))
        for name in LEGACY_SERVICES - {"accounting-source-db"}:
            serialized = json.dumps(services[name], sort_keys=True)
            for marker in MIGRATION_TARGET_MARKERS[1:]:
                self.assertIn(marker, serialized, (name, marker))
            self.assertIn("/mnt/accounting-source", serialized)

    def test_wrapper_refuses_production_transition_and_weak_confirmation(self) -> None:
        for project in (
            "usl-odoo-production",
            "usl-odoo-preprod-1234",
            "usl-odoo-transition-live-20260828",
        ):
            with self.subTest(project=project):
                environment = os.environ.copy()
                environment["COMPOSE_PROJECT_NAME"] = project
                result = run(str(WRAPPER), "config", env=environment)
                self.assertEqual(2, result.returncode)
                self.assertIn("production, pre-production, canonical, or transition-live", result.stderr)

        environment = os.environ.copy()
        environment["COMPOSE_PROJECT_NAME"] = "usl-odoo-migration-legacy-test"
        result = run(str(WRAPPER), "config", env=environment)
        self.assertEqual(2, result.returncode)
        self.assertIn("USL_MIGRATION_LEGACY_CONFIRM", result.stderr)

    def test_wrapper_renders_only_after_exact_source_and_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            (source / "filestore").mkdir()
            dump = source / "dump.sql"
            dump.write_bytes(b"frozen forensic source\n")
            digest = hashlib.sha256(dump.read_bytes()).hexdigest()
            project = "usl-odoo-migration-legacy-test"
            environment = os.environ.copy()
            environment.update(
                {
                    "COMPOSE_PROJECT_NAME": project,
                    "USL_MIGRATION_LEGACY_CONFIRM": f"migration-legacy:{project}",
                    "USL_MIGRATION_LEGACY_SOURCE_SHA256": digest,
                    "USL_ONLINE_DUMP_DIR": str(source),
                },
            )
            result = run(str(WRAPPER), "config", env=environment)
        self.assertEqual(0, result.returncode, result.stderr)
        services = json.loads(result.stdout)["services"]
        self.assertTrue(LEGACY_SERVICES.issubset(services))


if __name__ == "__main__":
    unittest.main()
