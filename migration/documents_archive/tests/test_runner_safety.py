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
        self.assertNotIn("QUALIFIED_SOURCE", restore)
        self.assertIn('SOURCE_DUMP_SHA256 = os.environ["DOCUMENTS_SOURCE_DUMP_SHA256"]', restore)
        self.assertIn("source contains unsupported Documents URL references", restore)

    def test_focused_restore_rejects_a_finalized_target_before_paperless_changes(self):
        script = SCRIPT.read_text(encoding="utf-8")

        self.assertIn("require_source_bindings", script)
        self.assertIn("migration source bindings are absent", script)
        self.assertIn("make qa-cache-refresh", script)
        self.assertLess(
            script.index("require_source_bindings\n        start_archive"),
            script.index("verify_checkpoint\n        run_restore"),
        )

    def test_reconstruction_validates_identity_project_before_database_work(self):
        script = TARGET_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("preflight_target_identity", script)
        self.assertIn("Pocket ID configuration belongs to another project", script)
        self.assertLess(
            script.index('run_stage "target identity preflight"'),
            script.index("start_target_database\nstop_product"),
        )

    def test_qa_refresh_can_resume_only_after_exact_accounting_revalidation(self):
        script = TARGET_SCRIPT.read_text(encoding="utf-8")
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

        self.assertIn("qa-cache-resume:", makefile)
        self.assertIn("USL_RECONSTRUCT_RESUME_ACCOUNTING=1", makefile)
        self.assertIn('run_stage "revalidate reusable accounting"', script)
        self.assertIn("scripts/accounting-compat dev-validate", script)
        self.assertIn('failed_checks == {"manager_accounting_identity_matches"}', script)
        self.assertIn('manager.get("target") is None', script)
        self.assertIn("scripts/hr-restore all", script)
        self.assertIn("Production migration cannot resume", script)
        self.assertIn("Accounting remains validated", script)

    def test_documents_migration_workers_are_bounded_and_production_is_conservative(self):
        runner = SCRIPT.read_text(encoding="utf-8")
        target = TARGET_SCRIPT.read_text(encoding="utf-8")
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")

        self.assertIn("Documents Paperless task workers must be between 1 and 4", runner)
        self.assertIn('PAPERLESS_TASK_WORKERS="$paperless_task_workers"', runner)
        self.assertIn("PAPERLESS_TASK_WORKERS: ${PAPERLESS_TASK_WORKERS:-1}", compose)
        self.assertIn('USL_DOCUMENTS_TASK_WORKERS:-1', target)
        self.assertIn('USL_DOCUMENTS_TASK_WORKERS:-3', target)

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
        self.assertIn("pg_restore -U odoo -d odoo_dev --exit-on-error", script)
        self.assertIn('--jobs="$RESTORE_JOBS"', script)
        self.assertIn("TaskResult.objects.count()", script)
        self.assertIn("verify_hydrated_controls", script)

    def test_partial_profiles_are_explicit_and_never_reuse_checkpoint(self):
        target = TARGET_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("no-documents", target)
        self.assertIn("documents-smoke", target)
        self.assertIn("Paperless checkpoint reuse is only valid", target)
        self.assertIn('USL_QA_DATA_PROFILE="$qa_profile"', target)

    def test_production_migration_is_source_wide_fresh_and_confirmed(self):
        target = TARGET_SCRIPT.read_text(encoding="utf-8")
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        qa = QA_SCRIPT.read_text(encoding="utf-8")
        preprod = (ROOT / "scripts/preprod-release").read_text(encoding="utf-8")

        self.assertIn('migration_purpose="${USL_MIGRATION_PURPOSE:-production}"', target)
        self.assertIn("source_gate=gate", target)
        self.assertIn("attachment_gate=gate", target)
        self.assertIn("USL_MIGRATION_CONFIRM_SOURCE_SHA", target)
        self.assertIn('export USL_ONLINE_DUMP_DIR="$source_dump_dir"', target)
        self.assertLess(
            target.index('export USL_ONLINE_DUMP_DIR="$source_dump_dir"'),
            target.index('scripts/migration-source-truth "$source_gate"'),
        )
        self.assertLess(
            target.index('scripts/migration-source-truth "$source_gate"'),
            target.index("scripts/accounting-compat dev-reset"),
        )
        self.assertLess(
            target.index('scripts/attachment-ledger "$attachment_gate"'),
            target.index("scripts/accounting-compat dev-reset"),
        )
        self.assertIn("migrate-production:", makefile)
        self.assertIn('USL_MIGRATION_CONFIRM_SOURCE_SHA="$(SOURCE_SHA)"', makefile)
        self.assertIn("USL_MIGRATION_PURPOSE=development", qa)
        self.assertIn("USL_MIGRATION_PURPOSE=production", preprod)
        self.assertIn('stage "multi-company acceptance"', qa)

        finalizer = (ROOT / "scripts/target-finalize").read_text(encoding="utf-8")
        self.assertIn("scripts/platform-billing-restore product-validate", finalizer)
        self.assertIn(
            'usl_cli_load_local_port_defaults "$ROOT" "${POCKET_ID_ENV_FILE:-}"',
            finalizer,
        )

    def test_seed_pruning_requires_confirmation_and_preserves_current(self):
        seed = SEED_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("USL_QA_SEED_PRUNE_CONFIRM", seed)
        self.assertIn('[[ "$candidate" != "$current_dir" ]]', seed)
        self.assertIn("CONFIRM=qa-seeds", seed)

    def test_source_identity_index_is_removed_with_migration_columns(self):
        finalizer = (
            ROOT / "migration/accounting_restore/scripts/finalize_schema.py"
        ).read_text(encoding="utf-8")

        self.assertIn("_rebuild_source_identity_uniq", finalizer)
        self.assertIn("remaining_source_identity_indexes", finalizer)
        self.assertLess(
            finalizer.index("source_identity_indexes ="),
            finalizer.index("ALTER TABLE {} DROP COLUMN {}"),
        )
        self.assertGreater(
            finalizer.index("remaining_source_identity_indexes ="),
            finalizer.index("ALTER TABLE {} DROP COLUMN {}"),
        )

    def test_external_pocket_overlay_never_manages_identity_service(self):
        overlay = (ROOT / "compose.external-pocket-id.yaml").read_text(
            encoding="utf-8",
        )

        self.assertNotIn("  pocket-id:", overlay)
        self.assertNotIn("pocket-id-data", overlay)
        self.assertIn("external-identity:", overlay)
        self.assertIn("external-ingress:", overlay)
        self.assertIn('127.0.0.1:${ODOO_HTTP_PORT}:8069', overlay)
        self.assertIn('127.0.0.1:${PAPERLESS_HTTP_PORT}:8000', overlay)

    def test_portable_candidate_and_cutover_are_fail_closed(self):
        candidate = (ROOT / "scripts/migration-candidate").read_text(
            encoding="utf-8",
        )
        cutover = (ROOT / "scripts/production-cutover").read_text(
            encoding="utf-8",
        )

        self.assertIn("source_coverage", candidate)
        self.assertIn("attachment_coverage", candidate)
        self.assertIn("--portable-candidate", candidate)
        self.assertIn("pg_dump -U \"$POSTGRES_USER\" -Fc", candidate)
        self.assertNotIn("compose.pocket-id.yaml", cutover)
        self.assertIn("candidate reset is permanently disabled", cutover.lower())
        self.assertIn("document_importer --no-progress-bar", cutover)
        self.assertIn('--jobs="$RESTORE_JOBS"', cutover)
        self.assertIn("USL_PRODUCTION_CRON_ALLOWLIST_JSON", cutover)
        self.assertIn("journeys --evidence", cutover)


if __name__ == "__main__":
    unittest.main()
