from __future__ import annotations

import hashlib
import os
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from accounting_compat.cli import (
    build_parser,
    classify_product_import_failure,
    configure_source_mount,
    git_tracking_status,
    source_snapshot_id,
    source_validation_manifest,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


class OdooTestHarnessTest(unittest.TestCase):
    def test_source_restore_recreates_service_after_network_rotation(self):
        harness = (REPOSITORY_ROOT / "accounting_compat" / "cli.py").read_text(
            encoding="utf-8",
        )
        restore = harness.split("\ndef restore_source(", 1)[1].split(
            "\ndef installed_modules(",
            1,
        )[0]

        self.assertIn(
            'compose_args("up", "-d", "--force-recreate", SOURCE_DB_SERVICE)',
            restore,
        )

    def test_import_exit_137_is_reported_as_resource_exhaustion(self):
        evidence = classify_product_import_failure(137, "registry loaded\nKilled\n")

        self.assertEqual(
            evidence["classification"],
            "MIGRATION_RESOURCE_EXHAUSTION",
        )
        self.assertEqual(evidence["failure_mode"], "process_killed")
        self.assertIn("reset", evidence["recovery"])

    def test_ordinary_import_failure_remains_a_product_defect(self):
        evidence = classify_product_import_failure(1, "Traceback: invalid source")

        self.assertEqual(
            evidence["classification"],
            "SOURCE_SNAPSHOT_PRODUCT_IMPORT_DEFECT",
        )

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

    def test_git_tracking_audit_degrades_when_git_is_unavailable(self):
        with patch("accounting_compat.cli.shutil.which", return_value=None):
            records = git_tracking_status([REPOSITORY_ROOT / "README.md"])

        self.assertEqual(
            records,
            [{
                "path": "README.md",
                "tracked": False,
                "ignored": False,
                "ignore_rule": None,
            }],
        )

    def test_source_argument_and_compose_mount_use_the_same_external_path(self):
        with TemporaryDirectory() as temporary_directory:
            source_directory = Path(temporary_directory).resolve()
            with patch.dict(
                os.environ,
                {"USL_ONLINE_DUMP_DIR": str(source_directory)},
            ):
                args = build_parser().parse_args(["source-validate"])
                configure_source_mount(args.source_dir)

                self.assertEqual(args.source_dir, str(source_directory))
                self.assertEqual(
                    os.environ["USL_ONLINE_DUMP_DIR"],
                    str(source_directory),
                )

    def test_snapshot_id_uses_the_selected_external_dump(self):
        with TemporaryDirectory() as temporary_directory:
            source_directory = Path(temporary_directory).resolve()
            dump_content = b"-- selected external PostgreSQL dump\n"
            (source_directory / "dump.sql").write_bytes(dump_content)
            (source_directory / "filestore").mkdir()
            expected_digest = hashlib.sha256(dump_content).hexdigest()[:12]

            with patch.dict(
                os.environ,
                {"USL_ONLINE_DUMP_DIR": str(source_directory)},
            ):
                self.assertEqual(
                    source_snapshot_id(),
                    f"source-{expected_digest}",
                )

    def test_source_manifest_accepts_an_external_absolute_dump_path(self):
        with TemporaryDirectory() as temporary_directory:
            source_directory = (
                Path(temporary_directory) / "external-source"
            ).resolve()
            source_directory.mkdir()
            dump_path = source_directory / "dump.sql"
            dump_path.write_text("-- PostgreSQL database dump\n", encoding="utf-8")
            (source_directory / "filestore").mkdir()

            manifest = source_validation_manifest(str(source_directory))

        self.assertEqual(manifest["status"], "passed")
        self.assertEqual(manifest["source_dir"], str(source_directory))
        self.assertEqual(manifest["dump"]["path"], str(dump_path))
        self.assertEqual(
            manifest["git_tracking"][0],
            {
                "path": str(source_directory),
                "tracked": False,
                "ignored": False,
                "ignore_rule": None,
            },
        )

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

    def test_platform_restore_tests_use_browser_capable_image(self):
        helper = (
            REPOSITORY_ROOT / "scripts" / "platform-billing-restore"
        ).read_text(encoding="utf-8")
        makefile = (REPOSITORY_ROOT / "Makefile").read_text(encoding="utf-8")
        test_restore = helper.split("\ntest_restore() {\n", 1)[1].split(
            "\ncase ",
            1,
        )[0]

        self.assertIn("compose build odoo", test_restore)
        self.assertIn("compose --profile test build test", test_restore)
        self.assertIn("compose --profile test run --rm", test_restore)
        self.assertIn("platform-billing-migration-addons:ro", test_restore)
        self.assertIn("test \\", test_restore)
        self.assertNotIn("platform-billing-migration \\", test_restore)
        self.assertIn(
            "COMPOSE_PROJECT_NAME=$(COMPOSE_PROJECT) "
            "scripts/platform-billing-restore test",
            makefile,
        )

    def test_platform_qa_helpers_require_an_isolated_compose_project(self):
        helper = (REPOSITORY_ROOT / "scripts" / "odoo-dev").read_text(
            encoding="utf-8",
        )
        scope_guard = helper.split(
            "\nverify_platform_billing_qa_scope() {\n",
            1,
        )[1].split("\n}\n", 1)[0]
        audit_helper = helper.split(
            "\naudit_platform_billing_qa() {\n",
            1,
        )[1].split("\n}\n", 1)[0]
        bootstrap_helper = helper.split(
            "\nbootstrap_platform_billing_qa() {\n",
            1,
        )[1].split("\n}\n", 1)[0]

        self.assertIn('"$COMPOSE_PROJECT" != usl-odoo-fp-*', scope_guard)
        self.assertIn('"${COMPOSE_PROJECT_NAME:-}"', scope_guard)
        self.assertIn("com.docker.compose.project.working_dir", scope_guard)
        self.assertGreaterEqual(audit_helper.count("platform_billing_compose"), 3)
        self.assertIn("verify_platform_billing_qa_scope", audit_helper)
        self.assertGreaterEqual(
            bootstrap_helper.count("platform_billing_compose"),
            3,
        )
        self.assertIn("verify_platform_billing_qa_scope", bootstrap_helper)

    def test_worktree_helpers_guard_shared_compose_ownership(self):
        guard = (
            REPOSITORY_ROOT / "scripts" / "lib" / "compose-scope.sh"
        ).read_text(encoding="utf-8")
        self.assertIn("com.docker.compose.project.working_dir", guard)
        self.assertIn('[[ -f "$repository_root/.git"', guard)

        for relative_path in (
            "scripts/accounting-restore",
            "scripts/identity-restore",
            "scripts/product-restore",
            "scripts/hr-restore",
            "scripts/project-restore",
            "scripts/tese-restore",
            "scripts/platform-billing-restore",
            "scripts/documents-restore",
            "scripts/documents-stack",
            "scripts/odoo-dev",
            "scripts/pocket-id-dev",
            "scripts/target-finalize",
            "scripts/target-reconstruct",
        ):
            helper = (REPOSITORY_ROOT / relative_path).read_text(
                encoding="utf-8",
            )
            self.assertIn("compose-scope.sh", helper, relative_path)
            self.assertIn("usl_verify_compose_scope", helper, relative_path)

    def test_full_reconstruction_requires_an_explicit_project(self):
        result = subprocess.run(
            [str(REPOSITORY_ROOT / "scripts" / "target-reconstruct")],
            cwd=REPOSITORY_ROOT,
            env={
                key: value
                for key, value in os.environ.items()
                if key != "COMPOSE_PROJECT_NAME"
            },
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("requires an explicit COMPOSE_PROJECT_NAME", result.stderr)

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
        self.assertIn("odoo_online_source_saas_19_3", disable_helper)
        self.assertIn("USL_EINVOICE_LIVE_ENABLED", disable_helper)
        self.assertIn("USL_EREPORTING_LIVE_ENABLED", disable_helper)

    def test_expense_batch_qa_bootstrap_uses_defined_target_guard(self):
        helper = (REPOSITORY_ROOT / "scripts" / "odoo-dev").read_text(
            encoding="utf-8",
        )

        self.assertIn('if [[ "$DEV_DB" != "odoo_dev" ]]', helper)
        self.assertIn('"${COMPOSE[@]}" up -d --wait db', helper)
        self.assertNotIn("require_target_database", helper)

        bootstrap = (
            REPOSITORY_ROOT / "scripts" / "odoo" / "expense_batch_qa_bootstrap.py"
        ).read_text(encoding="utf-8")
        self.assertIn("def _active_company_payment_method", bootstrap)
        self.assertIn('(\"payment_account_id.active\", \"=\", True)', bootstrap)
        self.assertIn("payment_method_line=company_payment_method", bootstrap)

    def test_worktree_odoo_dev_restores_its_pocket_id_runtime(self):
        helper = (REPOSITORY_ROOT / "scripts" / "odoo-dev").read_text(
            encoding="utf-8",
        )
        runtime_guard = helper.split(
            "\nuses_pocket_id_runtime() {\n",
            1,
        )[1].split("\n}\n", 1)[0]
        restore = helper.split(
            "\nrestore_development_runtime() {\n",
            1,
        )[1].split("\n}\n", 1)[0]

        self.assertIn('[[ "$DEV_DB" == "odoo_dev" ]]', runtime_guard)
        self.assertIn('[[ -f "$ROOT/.git" ]]', runtime_guard)
        self.assertIn("pocket_id_env_project", runtime_guard)
        self.assertIn("uses_pocket_id_runtime", restore)
        self.assertIn('"$ROOT/scripts/pocket-id-dev" start-runtime', restore)
        self.assertGreaterEqual(helper.count("if uses_pocket_id_runtime"), 8)
        self.assertIn('"$ROOT/scripts/pocket-id-dev" update-odoo', helper)

        pocket_helper = (
            REPOSITORY_ROOT / "scripts" / "pocket-id-dev"
        ).read_text(encoding="utf-8")
        self.assertIn('local skip_paperless="${2:-0}"', pocket_helper)
        self.assertIn('local apply_identity_policy="${3:-1}"', pocket_helper)
        self.assertIn('configure_odoo "${2:-usl_pocketid}" 1 1', pocket_helper)
        self.assertIn('configure_odoo "${2:-usl_pocketid}" 0 0', pocket_helper)
        self.assertIn('[[ "$apply_identity_policy" == "0" ]]', pocket_helper)
        verify_runtime = pocket_helper.split(
            "\nverify_odoo_runtime() {\n",
            1,
        )[1].split("\n}\n", 1)[0]
        self.assertIn("load_environment 0", verify_runtime)

    def test_login_link_refuses_a_broken_odoo_pocket_runtime(self):
        helper = (REPOSITORY_ROOT / "scripts" / "pocket-id-dev").read_text(
            encoding="utf-8",
        )
        makefile = (REPOSITORY_ROOT / "Makefile").read_text(encoding="utf-8")
        one_time_link = helper.split("\none_time_link() {\n", 1)[1].split(
            "\n}\n",
            1,
        )[0]
        doctor = (
            REPOSITORY_ROOT / "scripts" / "odoo-dev"
        ).read_text(encoding="utf-8").split("\ndoctor() {\n", 1)[1].split(
            "\n}\n",
            1,
        )[0]

        self.assertLess(
            one_time_link.index("verify_odoo_runtime"),
            one_time_link.index('one-time-link "$username"'),
        )
        self.assertIn("make COMPOSE_PROJECT=%s repair-pocket-id", one_time_link)
        self.assertIn("Pocket ID: %s", doctor)
        self.assertIn("Pocket ID repair", doctor)
        self.assertIn("repair-pocket-id:", makefile)

    def test_pocket_id_runtime_check_compares_process_and_database(self):
        runtime_check = (
            REPOSITORY_ROOT / "scripts" / "odoo" / "pocket_id_runtime_check.py"
        ).read_text(encoding="utf-8")

        self.assertIn('env.ref("usl_pocketid.provider_pocketid"', runtime_check)
        self.assertIn('"USL_POCKET_ID_CLIENT_SECRET"', runtime_check)
        self.assertIn('"usl_public_base_url"', runtime_check)
        self.assertIn('"usl_required_group"', runtime_check)
        self.assertIn("POCKET_ID_RUNTIME_CHECK=", runtime_check)
        self.assertIn('"secret_present": True', runtime_check)

        repair = (
            REPOSITORY_ROOT / "scripts" / "odoo" / "pocket_id_runtime_repair.py"
        ).read_text(encoding="utf-8")
        self.assertIn("_usl_pocketid_apply_environment", repair)
        self.assertNotIn("_usl_pocketid_apply_user_configuration", repair)

    def test_reconstruction_repairs_trip_products_after_product_restore(self):
        reconstruction = (REPOSITORY_ROOT / "scripts" / "target-reconstruct").read_text(
            encoding="utf-8",
        )
        accounting_restore = (
            REPOSITORY_ROOT / "scripts" / "accounting-restore"
        ).read_text(encoding="utf-8")
        repair_script = (
            REPOSITORY_ROOT
            / "migration/accounting_restore/addons/usl_accounting_restore/scripts"
            / "reapply_expense_batch_transition.py"
        ).read_text(encoding="utf-8")
        execution = reconstruction.split(
            'run_stage "target identity preflight"',
            1,
        )[1]
        product_restore = execution.index('run_stage "restore product data"')
        transition = execution.index(
            'run_stage "repair Expense Batch transition"',
        )
        b2c_restore = execution.index('run_stage "restore B2C commerce evidence"')
        hr_restore = execution.index('run_stage "restore HR"')

        self.assertLess(product_restore, transition)
        self.assertLess(transition, b2c_restore)
        self.assertLess(b2c_restore, hr_restore)
        self.assertIn("expense-batch-transition)", accounting_restore)
        self.assertEqual(repair_script.count("run_expense_batch_transition()"), 2)
        self.assertIn('"rerun_is_noop"', repair_script)
        self.assertIn('"trip_products_archived"', repair_script)


if __name__ == "__main__":
    unittest.main()
