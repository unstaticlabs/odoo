import os
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts/documents-restore"
QA_SCRIPT = ROOT / "scripts/qa-environment"
SEED_SCRIPT = ROOT / "scripts/qa-seed"
TARGET_SCRIPT = ROOT / "scripts/target-reconstruct"


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
        for database in ("", "odoo_online_source_saas_19_3"):
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
            "--update=rebuild_account_migration,usl_documents,usl_documents_accounting",
            script,
        )

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

    def test_canonical_reset_clears_target_mirror_before_archive_volumes(self):
        script = SCRIPT.read_text(encoding="utf-8")
        reset = (
            ROOT / "migration/documents_archive/scripts/reset_target_cache.py"
        ).read_text(encoding="utf-8")

        self.assertLess(
            script.index("reset_target_cache.py"),
            script.index('"${compose[@]}" --profile paperless rm -sf'),
        )
        self.assertIn("DOCUMENTS_CANONICAL_RESET_CONFIRMED=1", script)
        self.assertIn('env.cr.dbname != "odoo_dev"', reset)
        self.assertIn('env["usl.document.operation"]', reset)
        self.assertIn('env["usl.document.link"]', reset)
        self.assertIn('env["usl.document"]', reset)

    def test_restore_uses_all_mapped_companies_for_archive_policy(self):
        restore = (
            ROOT
            / "migration/documents_archive/scripts/source_documents_restore.py"
        ).read_text(encoding="utf-8")

        self.assertIn("allowed_company_ids=target_companies.ids", restore)
        self.assertIn("documents_model.with_env(admin.env)", restore)
        self.assertIn('item["document"].with_env(admin.env)', restore)

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
