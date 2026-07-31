from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from accounting_compat.cli import source_snapshot_id, source_validation_manifest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


class OdooTestHarnessTest(unittest.TestCase):
    def test_source_validation_accepts_external_absolute_package_path(self):
        with TemporaryDirectory() as directory:
            package = Path(directory)
            dump = package / "dump.sql"
            dump.write_text("-- PostgreSQL database dump\n", encoding="utf-8")
            (package / "filestore").mkdir()

            manifest = source_validation_manifest(str(package))
            snapshot_id = source_snapshot_id(str(package))

        self.assertEqual(manifest["status"], "passed")
        self.assertEqual(manifest["source_dir"], str(package.resolve()))
        self.assertEqual(manifest["dump"]["path"], str(dump.resolve()))
        self.assertTrue(snapshot_id.startswith("source-"))

    def test_test_service_receives_selected_database_filter(self):
        compose = (REPOSITORY_ROOT / "compose.yaml").read_text(encoding="utf-8")
        test_service = compose.split("\n  test:\n", 1)[1].split(
            "\n  devcontainer:\n",
            1,
        )[0]

        self.assertIn(
            "ODOO_DB_FILTER: ${ODOO_DB_FILTER:-^odoo_dev$}",
            test_service,
        )

    def test_module_and_tag_commands_use_browser_capable_image(self):
        helper = (REPOSITORY_ROOT / "scripts" / "odoo-dev").read_text(
            encoding="utf-8",
        )
        test_module = helper.split("\ntest_module() {\n", 1)[1].split(
            "\ncase ",
            1,
        )[0]
        test_tag = helper.split("\ntest_tag() {\n", 1)[1].split(
            "\nbootstrap_einvoice_qa() ",
            1,
        )[0]

        self.assertIn('"${COMPOSE[@]}" --profile test build test', test_module)
        self.assertIn('"${COMPOSE[@]}" --profile test run --rm', test_module)
        self.assertIn('-e ODOO_INIT_DB="$database"', test_module)
        self.assertIn('-e ODOO_DB_FILTER="^${database}$"', test_module)
        self.assertIn("test odoo", test_module)

        self.assertIn('"${COMPOSE[@]}" --profile test build test', test_tag)
        self.assertIn("run_with_odoo_stopped test test odoo", test_tag)

    def test_dev_lifecycle_disables_automatic_tours(self):
        helper = (REPOSITORY_ROOT / "scripts" / "odoo-dev").read_text(
            encoding="utf-8",
        )
        makefile = (REPOSITORY_ROOT / "Makefile").read_text(encoding="utf-8")
        finalizer = (REPOSITORY_ROOT / "scripts" / "target-finalize").read_text(
            encoding="utf-8",
        )
        disable_helper = (
            REPOSITORY_ROOT / "scripts" / "odoo" / "disable_dev_tours.py"
        ).read_text(encoding="utf-8")

        self.assertIn("disable_dev_tours() {", helper)
        self.assertGreaterEqual(helper.count("disable_dev_tours"), 5)
        self.assertIn("-e USL_DISABLE_DEV_TOURS=1", helper)
        self.assertIn('"$ROOT/scripts/odoo/disable_dev_tours.py"', helper)
        self.assertIn("disable-tours:", makefile)
        self.assertIn("scripts/odoo-dev disable-tours", finalizer)
        self.assertIn('users.write({"tour_enabled": False})', disable_helper)
        self.assertNotIn("user_consumed_ids", disable_helper)
        self.assertNotIn("Command.link", disable_helper)
        self.assertIn("odoo_online_source_saas_19_2", disable_helper)
        self.assertIn("USL_EINVOICE_LIVE_ENABLED", disable_helper)
        self.assertIn("USL_EREPORTING_LIVE_ENABLED", disable_helper)


if __name__ == "__main__":
    unittest.main()
