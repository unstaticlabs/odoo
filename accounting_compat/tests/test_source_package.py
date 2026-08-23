from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from accounting_compat.cli import (
    configure_source_mount,
    resolve_compose_project,
    source_validation_manifest,
)


class SourcePackageTest(unittest.TestCase):
    def test_source_argument_also_configures_the_read_only_compose_mount(self):
        with tempfile.TemporaryDirectory() as directory:
            environment = {"USL_ONLINE_DUMP_DIR": "/stale/default"}

            package = configure_source_mount(directory, environment)

        self.assertEqual(package.path, Path(directory).resolve())
        self.assertEqual(environment["USL_ONLINE_DUMP_DIR"], str(package.path))

    def test_compose_project_prefers_explicit_isolation_variables(self):
        self.assertEqual(
            resolve_compose_project(
                {
                    "ACCOUNTING_COMPAT_COMPOSE_PROJECT": "explicit",
                    "COMPOSE_PROJECT_NAME": "compose",
                    "ODOO_SAAS_COMPOSE_PROJECT": "legacy",
                },
            ),
            "explicit",
        )
        self.assertEqual(
            resolve_compose_project(
                {
                    "COMPOSE_PROJECT_NAME": "compose",
                    "ODOO_SAAS_COMPOSE_PROJECT": "legacy",
                },
            ),
            "compose",
        )
        self.assertEqual(
            resolve_compose_project(
                {"ODOO_SAAS_COMPOSE_PROJECT": "legacy"},
            ),
            "legacy",
        )

    def test_external_source_package_uses_absolute_evidence_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            (source / "dump.sql").write_text(
                "-- PostgreSQL database dump\n"
                "-- Dumped from database version 16.14\n"
                "-- Dumped by pg_dump version 16.14\n",
                encoding="utf-8",
            )
            (source / "filestore").mkdir()

            manifest = source_validation_manifest(str(source))

        self.assertEqual(manifest["status"], "passed")
        self.assertEqual(manifest["source_dir"], str(source.resolve()))
        self.assertEqual(manifest["dump"]["path"], str((source / "dump.sql").resolve()))


if __name__ == "__main__":
    unittest.main()
