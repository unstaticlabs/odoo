import os
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts/documents-restore"
QA_SCRIPT = ROOT / "scripts/qa-environment"
SEED_SCRIPT = ROOT / "scripts/qa-seed"
TARGET_SCRIPT = ROOT / "scripts/target-reconstruct"
NATIVE_BRIDGE_SCRIPT = (
    ROOT / "migration/documents_archive/scripts/reconcile_native_attachments.py"
)
PAPERLESS_MIGRATION_ACCESS = (
    ROOT / "migration/documents_archive/scripts/paperless_migration_access.py"
)
PAPERLESS_MIGRATION_ACCESS_CLEANUP = (
    ROOT
    / "migration/documents_archive/scripts/paperless_migration_access_cleanup.py"
)


class DocumentsRunnerSafetyTest(unittest.TestCase):
    def run_runner(self, **environment):
        return subprocess.run(
            [str(SCRIPT), "status"],
            cwd=ROOT,
            env={**os.environ, **environment},
            capture_output=True,
            text=True,
            check=False,
        )

    def test_rejects_non_migration_compose_project(self):
        completed = self.run_runner(COMPOSE_PROJECT_NAME="main-development")
        self.assertEqual(completed.returncode, 2)
        self.assertIn("Refusing non-isolated", completed.stderr)

    def test_rejects_empty_or_protected_target_database(self):
        for database in ("", "odoo_online_source_saas_19_2"):
            with self.subTest(database=database):
                completed = self.run_runner(
                    COMPOSE_PROJECT_NAME="codex-migration-safety-test",
                    DOCUMENTS_TARGET_DATABASE=database,
                )
                self.assertEqual(completed.returncode, 2)
                self.assertIn("Refusing unsafe", completed.stderr)

    def test_rejects_reserved_main_and_feature_ports(self):
        for variable, port in (
            ("DOCUMENTS_MIGRATION_PAPERLESS_PORT", "8010"),
            ("DOCUMENTS_MIGRATION_PAPERLESS_PORT", "18010"),
            ("DOCUMENTS_MIGRATION_ODOO_PORT", "8069"),
            ("DOCUMENTS_MIGRATION_ODOO_PORT", "18080"),
            ("DOCUMENTS_MIGRATION_ODOO_GEVENT_PORT", "8072"),
            ("DOCUMENTS_MIGRATION_ODOO_GEVENT_PORT", "18072"),
        ):
            with self.subTest(variable=variable, port=port):
                completed = self.run_runner(
                    COMPOSE_PROJECT_NAME="codex-migration-safety-test",
                    **{variable: port},
                )
                self.assertEqual(completed.returncode, 2)
                self.assertIn("Refusing reserved", completed.stderr)

    def test_upgrade_revalidates_accounting_parent_before_documents_view(self):
        script = SCRIPT.read_text(encoding="utf-8")

        self.assertIn(
            "--update=rebuild_account_migration,usl_documents,usl_expense_batch,"
            "usl_platform_billing,usl_tese_payroll,usl_documents_accounting",
            script,
        )

    def test_native_bridge_checkpoints_progress_before_final_gate(self):
        script = NATIVE_BRIDGE_SCRIPT.read_text(encoding="utf-8")

        guard = script.index("if blocking_operations or unaccounted:")
        failure = script.index("raise RuntimeError", guard)
        final_commit = script.index("env.cr.commit()", failure)
        self.assertLess(guard, failure)
        self.assertLess(failure, final_commit)
        self.assertIn(
            "Document.sync_from_paperless(full=True, client=migration_client)",
            script,
        )
        self.assertIn("bounded resumable queue checkpoint", script)
        self.assertIn("Commit every bounded worker pass", script)
        runner = SCRIPT.read_text(encoding="utf-8")
        self.assertIn(
            '-e DOCUMENTS_PAPERLESS_TOKEN="$documents_paperless_token"',
            runner,
        )

    def test_archive_migration_identity_is_temporary_and_fail_closed(self):
        access = PAPERLESS_MIGRATION_ACCESS.read_text(encoding="utf-8")
        cleanup = PAPERLESS_MIGRATION_ACCESS_CLEANUP.read_text(encoding="utf-8")
        runner = SCRIPT.read_text(encoding="utf-8")

        self.assertIn("user.is_superuser = True", access)
        self.assertIn("Token.objects.filter(user=user).delete()", cleanup)
        self.assertIn("UserObjectPermission.objects.filter(user=user).delete()", cleanup)
        self.assertIn("assign_owner=runtime_user", cleanup)
        self.assertIn("workflows.update(enabled=False)", cleanup)
        self.assertIn("user.is_active = False", cleanup)
        self.assertIn("user.is_superuser = False", cleanup)
        self.assertIn("trap deprovision_archive_identity EXIT", runner)

    def test_checkpoint_reuse_is_explicit_and_fail_closed(self):
        script = SCRIPT.read_text(encoding="utf-8")

        self.assertIn("DOCUMENTS_REQUIRE_CHECKPOINT", script)
        self.assertIn("verify_checkpoint", script)
        self.assertIn("seal_checkpoint", script)
        self.assertLess(
            script.index("verify_checkpoint\n        run_restore"),
            script.index("seal_checkpoint\n        ;;"),
        )
        self.assertIn(
            "A Documents run cannot reset and reuse the Paperless archive together.",
            script,
        )
        checkpoint = (
            ROOT / "migration/documents_archive/checkpoint.py"
        ).read_text(encoding="utf-8")
        self.assertIn("Run the normal fresh reconstruction", checkpoint)

    def test_fast_qa_verifies_seed_before_reset_and_uses_official_importer(self):
        script = QA_SCRIPT.read_text(encoding="utf-8")

        self.assertLess(
            script.index('stage "verify qualified seed (read only)"'),
            script.index('stage "reset isolated QA project"'),
        )
        self.assertIn(
            "document_importer --no-progress-bar /usr/src/paperless/export/qa-seed",
            script,
        )
        self.assertIn("usl-odoo-qa-?*", script)
        self.assertIn("No containers or data volumes were changed", script)

    def test_partial_profiles_are_explicit_and_never_reuse_checkpoint(self):
        target = TARGET_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("no-documents", target)
        self.assertIn("documents-smoke", target)
        self.assertIn("Paperless checkpoint reuse is only valid", target)
        self.assertIn('USL_QA_DATA_PROFILE="$qa_profile"', target)

    def test_seed_pruning_requires_confirmation_and_preserves_current(self):
        seed = SEED_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("USL_QA_SEED_PRUNE_CONFIRM", seed)
        self.assertIn('[[ "$candidate" != "$current_dir" ]]', seed)
        self.assertIn("CONFIRM=qa-seeds", seed)


if __name__ == "__main__":
    unittest.main()
