import ast
import os
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts/documents-restore"
QA_SCRIPT = ROOT / "scripts/qa-environment"
QA_CLEAN_SCRIPT = ROOT / "scripts/qa-clean"
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
RELEASE_INVENTORY = ROOT / "scripts/odoo/documents_release_inventory.py"
RELEASE_BUNDLE_SCRIPT = ROOT / "scripts/documents-release-bundle"
RECOVERY_SCRIPT = ROOT / "scripts/documents-recovery-test"
MIGRATION_CANDIDATE_SCRIPT = ROOT / "scripts/migration-candidate"
PRODUCTION_CUTOVER_SCRIPT = ROOT / "scripts/production-cutover"
EXPENSE_BATCH_MANIFEST = (
    ROOT / "custom-addons/usl_expense_batch/__manifest__.py"
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
        for database in ("", "odoo_online_source_saas_19_3"):
            with self.subTest(database=database):
                completed = self.run_runner(
                    COMPOSE_PROJECT_NAME="codex-migration-safety-test",
                    DOCUMENTS_TARGET_DATABASE=database,
                )
                self.assertEqual(completed.returncode, 2)
                self.assertIn("Refusing unsafe", completed.stderr)

    def test_rejects_invalid_restore_timeout(self):
        for value in ("", "0", "later"):
            with self.subTest(value=value):
                completed = self.run_runner(
                    COMPOSE_PROJECT_NAME="codex-migration-safety-test",
                    DOCUMENTS_RESTORE_TIMEOUT=value,
                )
                self.assertEqual(completed.returncode, 2)
                self.assertIn("positive number of seconds", completed.stderr)

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

    def test_canonical_compose_honors_project_extra_file(self):
        script = SCRIPT.read_text(encoding="utf-8")

        canonical_compose = script.index(
            '-f compose.yaml -f compose.pocket-id.yaml',
        )
        extra_file_guard = script.index(
            'if [[ -n "${USL_POCKET_ID_COMPOSE_EXTRA_FILE:-}" ]]',
            canonical_compose,
        )
        extra_file_append = script.index(
            'compose+=(-f "$USL_POCKET_ID_COMPOSE_EXTRA_FILE")',
            extra_file_guard,
        )
        self.assertLess(canonical_compose, extra_file_guard)
        self.assertLess(extra_file_guard, extra_file_append)

    def test_upgrade_revalidates_accounting_parent_before_documents_view(self):
        script = SCRIPT.read_text(encoding="utf-8")

        self.assertIn(
            "--update=rebuild_account_migration,usl_documents,usl_expense_batch,"
            "usl_platform_billing,usl_tese_payroll,usl_documents_accounting",
            script,
        )

    def test_expense_batch_declares_its_documents_adapter_dependency(self):
        manifest = ast.literal_eval(
            EXPENSE_BATCH_MANIFEST.read_text(encoding="utf-8"),
        )

        self.assertIn("usl_documents", manifest["depends"])

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
        self.assertIn("usl_documents_trusted_backfill_access=True", script)
        self.assertIn('(\"res_field\", \"=\", False)', script)
        self.assertIn('(\"res_field\", \"!=\", False)', script)
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
        self.assertIn("trap cleanup_documents_restore EXIT", runner)
        self.assertIn("deprovision_archive_identity || cleanup_status=$?", runner)
        self.assertIn("configure_runtime_archive_identity || cleanup_status=$?", runner)
        self.assertIn("deploy/documents/paperless_integration_access.py", runner)
        self.assertIn("scripts/odoo/documents_runtime_config.py", runner)
        self.assertIn("documents_runtime_paperless_token", runner)
        self.assertIn(
            '-e DOCUMENTS_PAPERLESS_SERVICE_USER_ID="$documents_paperless_service_user_id"',
            runner,
        )
        self.assertIn("restore_semantic_runtime || cleanup_status=$?", runner)

    def test_release_inventory_fails_closed_on_every_queue_and_boundary_counter(self):
        inventory = RELEASE_INVENTORY.read_text(encoding="utf-8")

        for name in (
            "eligible_attachment_pending",
            "eligible_attachment_unresolved",
            "odoo_operations_failed",
            "odoo_operations_pending",
            "odoo_operations_processing",
            "permission_failures",
            "migration_module_residue",
        ):
            self.assertIn(name, inventory)
        self.assertIn('USL_RELEASE_REQUIRE_COMPLETE") == "1"', inventory)
        self.assertIn("operation_failure_counts", inventory)
        self.assertIn("operation.acknowledged", inventory)
        self.assertIn("attachment.usl_documents_ledger_state", inventory)
        self.assertIn("resolved_or_acknowledged", inventory)
        self.assertNotIn("paperless_token\"", inventory.split("print(", 1)[-1])

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
        self.assertIn("seal-checkpoint)", script)
        self.assertIn(
            "Final archive checkpoint sealing requires the canonical full target.",
            script,
        )
        checkpoint = (
            ROOT / "migration/documents_archive/checkpoint.py"
        ).read_text(encoding="utf-8")
        self.assertIn("Run the normal fresh reconstruction", checkpoint)

    def test_bulk_restore_defers_then_verifies_semantic_index(self):
        script = SCRIPT.read_text(encoding="utf-8")
        qa_script = QA_SCRIPT.read_text(encoding="utf-8")
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")

        self.assertIn('DOCUMENTS_DEFER_SEMANTIC_INDEX:-1', script)
        self.assertIn("enable_semantic_index_deferral", script)
        self.assertIn("finalize_semantic_index", script)
        self.assertLess(
            script.index("run_restore\n        run_native_attachment_bridge"),
            script.index("run_native_attachment_bridge", script.index("case \"$command_name\"")),
        )
        self.assertLess(
            script.index("run_native_attachment_bridge\n        wait_for_archive_tasks"),
            script.index("finalize_semantic_index", script.index("case \"$command_name\"")),
        )
        for command in (
            "document_llmindex migrate",
            "document_llmindex update",
            "document_llmindex compact",
            "scripts/paperless_release_inventory.py",
        ):
            self.assertIn(command, script)
        self.assertIn("PAPERLESS_USL_DEFER_SEMANTIC_INDEX=true", script)
        self.assertIn("PAPERLESS_USL_DEFER_SEMANTIC_INDEX=false", script)
        self.assertIn("semantic-finalize)", script)
        self.assertIn("--force-recreate --no-deps paperless-webserver", script)
        self.assertIn("wait_for_archive_tasks", script)
        self.assertIn("documents_paperlesstask", script)
        self.assertIn(
            "Deferred semantic indexing is migration-only",
            compose,
        )
        self.assertIn("PAPERLESS_USL_DEFER_SEMANTIC_INDEX=true", qa_script)
        self.assertIn("scripts/documents-restore semantic-finalize", qa_script)
        self.assertLess(
            qa_script.index("enable Documents jobs and drain archive queue"),
            qa_script.index("restore and verify Documents semantic index"),
        )
        inventory = (
            ROOT / "scripts/paperless_release_inventory.py"
        ).read_text(encoding="utf-8")
        self.assertIn("vector_documents == live_documents", inventory)
        self.assertIn('"expected_indexed_documents": live_documents', inventory)
        bridge = (
            ROOT
            / "migration/documents_archive/scripts/reconcile_native_attachments.py"
        ).read_text(encoding="utf-8")
        self.assertLess(
            bridge.index("resume_deadline = time.monotonic() + 1800"),
            bridge.index("attachment_domain ="),
        )
        restore = (
            ROOT / "migration/documents_archive/scripts/source_documents_restore.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "existing_document_for(content_sha256, metadata_hash, company)",
            restore,
        )
        self.assertIn("_archive_fingerprint_version(", restore)

    def test_reconstruction_reseals_checkpoint_after_all_document_producers(self):
        target = TARGET_SCRIPT.read_text(encoding="utf-8")

        b2c_stage = target.index(
            'run_stage "finalize B2C relationships and Documents links"',
        )
        collaboration_stage = target.index(
            'run_stage "restore Collaboration history"',
        )
        checkpoint_stage = target.index(
            'run_stage "seal final Documents archive checkpoint"',
        )
        self.assertLess(b2c_stage, collaboration_stage)
        self.assertLess(collaboration_stage, checkpoint_stage)
        checkpoint = (
            ROOT / "migration/documents_archive/checkpoint.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            '"migration/collaboration_restore/addons/usl_collaboration_restore"',
            checkpoint,
        )

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
        self.assertIn('env.get("b2c.provider.evidence")', reset)
        self.assertIn("with_context(b2c_evidence_import=True).write", reset)
        self.assertLess(
            reset.index("with_context(b2c_evidence_import=True).write"),
            reset.index('Document.with_context(active_test=False).search([]).unlink()'),
        )
        self.assertIn("_clear_external_references", reset)
        self.assertIn("pg_constraint", reset)
        self.assertNotIn("pg_constraint AS constraint", reset)
        self.assertIn("attnotnull", reset)
        self.assertIn("Documents reset cannot detach required reference", reset)
        self.assertLess(
            reset.index('_clear_external_references(\n        "usl_document"'),
            reset.index("Operation.search([]).unlink()"),
        )

    def test_restore_uses_all_mapped_companies_for_archive_policy(self):
        restore = (
            ROOT
            / "migration/documents_archive/scripts/source_documents_restore.py"
        ).read_text(encoding="utf-8")

        self.assertIn("allowed_company_ids=target_companies.ids", restore)
        self.assertIn("allowed_company_ids=operation.company_id.ids", restore)
        self.assertIn("documents_model.with_env(admin.env)", restore)
        self.assertIn('item["document"].with_env(admin.env)', restore)
        self.assertNotIn("QUALIFIED_SOURCE", restore)
        self.assertIn('SOURCE_DUMP_SHA256 = os.environ["DOCUMENTS_SOURCE_DUMP_SHA256"]', restore)
        self.assertIn("source contains unsupported Documents URL references", restore)
        self.assertIn("attachment.res_model = 'ai.agent.source'", restore)
        self.assertIn('"restricted_unassigned_evidence"', restore)
        for model_name in (
            "b2c.accounting.session",
            "b2c.fulfilment.event",
            "b2c.order",
            "b2c.payment.event",
        ):
            self.assertIn(f'"{model_name}"', restore)
        self.assertIn("unsupported_relationships", restore)
        self.assertIn("preserved_governed_extension_relationship_count", restore)

    def test_repeated_restore_skips_unchanged_permission_writes(self):
        restore = (
            ROOT
            / "migration/documents_archive/scripts/source_documents_restore.py"
        ).read_text(encoding="utf-8")

        self.assertIn("def permissions_match(", restore)
        self.assertIn("if changed_documents:", restore)
        self.assertIn('"state": "accepted" if changed_documents else "unchanged"', restore)
        self.assertIn("load_remote_documents(changed_documents)", restore)

    def test_focused_restore_rejects_a_finalized_target_before_paperless_changes(self):
        script = SCRIPT.read_text(encoding="utf-8")

        self.assertIn("require_source_bindings", script)
        self.assertIn("migration source bindings are absent", script)
        self.assertIn("make qa-cache-refresh", script)
        self.assertLess(
            script.index(
                "require_source_bindings\n        enable_semantic_index_deferral",
            ),
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
        self.assertIn("scripts/hr-restore install", script)
        self.assertIn("scripts/hr-restore import", script)
        self.assertIn("scripts/hr-restore validate", script)
        self.assertIn("Production migration cannot resume", script)
        self.assertIn("Accounting remains validated", script)

    def test_finalized_qa_refresh_resumes_qualification_without_source_replay(self):
        script = TARGET_SCRIPT.read_text(encoding="utf-8")
        seed = SEED_SCRIPT.read_text(encoding="utf-8")
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

        self.assertIn("qa-cache-qualify-resume:", makefile)
        self.assertIn("USL_RECONSTRUCT_RESUME_FINALIZED=1", makefile)
        self.assertIn("revalidate_finalized_qa_cache", script)
        self.assertIn('migration_purpose" != qa-cache', script)
        self.assertIn('if [[ "$resume_finalized" == 1 ]]; then', script)
        finalized_branch = script[
            script.index('if [[ "$resume_finalized" == 1 ]]; then', script.index('start_target_database')):
            script.index('seed_refresh="${USL_QA_SEED_REFRESH:-0}"')
        ]
        self.assertIn('run_stage "revalidate finalized QA target"', finalized_branch)
        self.assertNotIn('run_stage "import accounting"', finalized_branch.split("else", 1)[0])
        self.assertIn('compose+=(--env-file "$POCKET_ID_ENV_FILE")', seed)
        self.assertLess(
            script.index('run_stage "apply target configuration"'),
            script.index('run_stage "capture pending QA seed"'),
        )
        self.assertLess(
            script.index('run_stage "capture pending QA seed"'),
            script.index('run_stage "restore target configuration after seed capture"'),
        )
        self.assertLess(
            script.index('run_stage "restore target configuration after seed capture"'),
            script.index('run_stage "multi-company acceptance"'),
        )

    def test_qa_reuses_only_an_exact_verified_worktree_state(self):
        script = QA_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("usl-worktree-qa-state-v1", script)
        self.assertIn("seed_manifest_sha256", script)
        self.assertIn("migration_sha256", script)
        self.assertIn("pocket_environment_sha256", script)
        self.assertIn("worktree_state_sha256", script)
        self.assertIn("qa_state_digest", script)
        self.assertIn("qa_volumes_present", script)
        self.assertIn('reuse_requested="${USL_QA_REUSE_EXISTING:-0}"', script)
        self.assertIn('cache_result="warm-hit"', script)
        self.assertLess(
            script.index("qa_state_matches && qa_volumes_present"),
            script.index('stage "reset isolated QA project"'),
        )
        self.assertIn('stage "reuse and verify existing QA target"', script)
        self.assertIn(
            "SELECT COALESCE((SELECT value FROM ir_config_parameter WHERE "
            "key='usl.qa.data_profile'), 'full')",
            script,
        )
        self.assertIn("scripts/check-product-database-boundary", script)

    def test_qa_cleanup_is_worktree_scoped_and_confirmation_gated(self):
        script = QA_CLEAN_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("usl-odoo-qa-?*", script)
        self.assertIn("USL_QA_CLEAN_CONFIRM", script)
        self.assertIn("qa-volumes", script)
        self.assertIn("usl_verify_compose_scope", script)
        self.assertIn("usl_compose_active_unsafe_resources", script)
        self.assertIn("--profile accounting-compat", script)
        self.assertIn("down --volumes --remove-orphans", script)
        self.assertNotIn("docker system prune", script)

    def test_downstream_source_bindings_survive_until_global_finalization(self):
        script = TARGET_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("restore_projects_for_reconstruction", script)
        self.assertIn("restore_tese_for_reconstruction", script)
        self.assertIn("restore_platform_billing_for_reconstruction", script)
        self.assertNotIn(
            'run_stage "restore Projects" env '
            "PROJECT_RESTORE_DEFER_PRODUCT_VALIDATE=1 scripts/project-restore all",
            script,
        )
        self.assertLess(
            script.index('run_stage "restore Documents archive"'),
            script.index('run_stage "restore Paie TESE"'),
        )
        self.assertLess(
            script.index('run_stage "restore Platform Billing"'),
            script.index('run_stage "restore Collaboration history"'),
        )
        self.assertLess(
            script.index('run_stage "restore Projects"'),
            script.index('run_stage "finalize source-backed saved preferences"'),
        )
        self.assertLess(
            script.index('run_stage "finalize source-backed saved preferences"'),
            script.index('run_stage "finalize migration boundary"'),
        )
        self.assertLess(
            script.index('run_stage "restore Collaboration history"'),
            script.index('run_stage "finalize migration boundary"'),
        )
        finalizer = script[
            script.index("finalize_migration_boundary()") :
            script.index('run_stage "Docker resource preflight"')
        ]
        self.assertIn(
            "PLATFORM_BILLING_RESTORE_DEFER_PRODUCT_FINALIZE=1",
            finalizer,
        )
        self.assertLess(
            finalizer.index("scripts/collaboration-restore finalize"),
            finalizer.index("scripts/platform-billing-restore finalize"),
        )
        self.assertLess(
            finalizer.index("scripts/platform-billing-restore finalize"),
            finalizer.index("scripts/tese-restore finalize"),
        )
        self.assertLess(
            finalizer.index("scripts/tese-restore finalize"),
            finalizer.index("scripts/project-restore finalize"),
        )
        self.assertLess(
            finalizer.index("scripts/project-restore finalize"),
            finalizer.index("scripts/hr-restore finalize"),
        )
        self.assertLess(
            finalizer.index("scripts/hr-restore finalize"),
            finalizer.index("scripts/product-restore finalize"),
        )
        self.assertLess(
            finalizer.index("scripts/product-restore finalize"),
            finalizer.index("scripts/identity-restore finalize"),
        )
        self.assertLess(
            finalizer.index("scripts/identity-restore finalize"),
            finalizer.index("scripts/accounting-restore finalize"),
        )
        self.assertLess(
            finalizer.index("scripts/accounting-restore finalize"),
            finalizer.index("scripts/platform-billing-restore schema-finalize"),
        )
        self.assertNotIn('run_stage "restore identities" scripts/identity-restore all', script)
        self.assertNotIn('run_stage "restore product data" scripts/product-restore all', script)
        self.assertNotIn('run_stage "restore HR" scripts/hr-restore all', script)

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
        self.assertIn("/usr/src/paperless/export/qa-seed", script)
        self.assertIn("usl-odoo-qa-?*", script)
        self.assertIn("No containers or data volumes were changed", script)
        self.assertIn("pg_restore -U odoo -d odoo_dev --exit-on-error", script)
        self.assertIn('--jobs="$RESTORE_JOBS"', script)
        self.assertIn("mkdir -p /target/filestore /target/sessions", script)
        self.assertIn("chown -R 1000:1000 /target", script)
        self.assertIn("PaperlessTask.objects.count()", script)
        self.assertIn("--user paperless --entrypoint python paperless-webserver", script)
        self.assertIn("manage.py document_importer --no-progress-bar", script)
        self.assertIn("verify_hydrated_controls", script)

    def test_paperless_file_writers_run_as_the_runtime_user(self):
        release = RELEASE_BUNDLE_SCRIPT.read_text(encoding="utf-8")
        recovery = RECOVERY_SCRIPT.read_text(encoding="utf-8")
        seed = SEED_SCRIPT.read_text(encoding="utf-8")
        candidate = MIGRATION_CANDIDATE_SCRIPT.read_text(encoding="utf-8")
        cutover = PRODUCTION_CUTOVER_SCRIPT.read_text(encoding="utf-8")

        self.assertGreaterEqual(
            release.count("exec -T --user paperless paperless-webserver"),
            9,
        )
        for command in (
            "document_index reindex",
            "document_index optimize",
            "document_llmindex migrate",
            "document_llmindex update",
            "document_llmindex compact",
            "document_exporter",
        ):
            self.assertIn(command, release)
        self.assertIn(
            "exec -T --user paperless paperless-webserver",
            recovery,
        )
        self.assertIn(
            "exec -T --user paperless paperless-webserver",
            seed,
        )
        self.assertGreaterEqual(
            candidate.count("exec -T --user paperless paperless-webserver"),
            3,
        )
        self.assertIn(
            "--user paperless --entrypoint python paperless-webserver",
            cutover,
        )

    def test_local_recovery_accepts_only_explicit_isolated_odoo_dev_scopes(self):
        recovery = RECOVERY_SCRIPT.read_text(encoding="utf-8")
        stack = (ROOT / "scripts/documents-stack").read_text(encoding="utf-8")

        for script in (recovery, stack):
            self.assertIn("USL_DOCUMENTS_ALLOW_ODOO_DEV_RECOVERY", script)
            self.assertIn("USL_DOCUMENTS_LOCAL_PREPROD_RECOVERY", script)
            self.assertIn("usl-odoo-preprod-*", script)
            self.assertIn("usl-odoo-paperless-*", script)
            self.assertNotIn('"$PROJECT" = "*"', script)
        self.assertIn("LOCAL_DEV_RECOVERY", recovery)
        self.assertIn("SOURCE_OVERRIDE=()", recovery)
        self.assertIn("COMPOSE_OVERRIDE=()", stack)
        self.assertIn("SOURCE_PAPERLESS_OLLAMA_VOLUME", recovery)
        self.assertIn("paperless-ollama-data.tgz", recovery)

    def test_clean_paperless_bootstrap_is_digest_pinned(self):
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
        stack = (ROOT / "scripts/documents-stack").read_text(encoding="utf-8")
        target = TARGET_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("paperless-model-init:", compose)
        self.assertIn('ollama pull "$$USL_BGE_SOURCE_MODEL"', compose)
        self.assertIn(
            'ollama cp "$$USL_BGE_SOURCE_MODEL" "$$USL_BGE_TARGET_MODEL"',
            compose,
        )
        self.assertGreaterEqual(
            compose.count(
                "7907646426070047a77226ac3e684fbbe8410524f7b4a74d02837e43f2146bab",
            ),
            1,
        )
        self.assertIn("paperless-model-init", stack)
        self.assertIn("Paperless BGE-M3 bootstrap digest is not pinned", stack)
        self.assertIn("prepare_personal_ai_keyring", target)
        self.assertIn(
            "Production migration requires USL_PERSONAL_AI_MASTER_KEYS_HOST_PATH",
            target,
        )
        self.assertIn('run_stage "Personal AI key preflight"', target)

    def test_qualified_document_service_pins_are_consistent(self):
        paths = (
            ROOT / ".env.example",
            ROOT / "compose.yaml",
            ROOT / "deploy/documents/preprod.env.example",
            ROOT / "deploy/documents/qa.env",
            ROOT / "deploy/preprod.env.example",
            ROOT / "deploy/production.external-pocket-id.env.example",
            ROOT / "scripts/documents-stack",
            ROOT / "scripts/pocket_id_dev.py",
        )
        content = "\n".join(path.read_text(encoding="utf-8") for path in paths)

        self.assertNotIn("gotenberg/gotenberg:8.35", content)
        self.assertNotIn("pocket-id:v2.13.0", content)
        self.assertIn(
            "gotenberg/gotenberg:8.36@"
            "sha256:87c16b9f364279d321bc9772d31fa58a"
            "a6abe036423c270698bd636c3a8e9466",
            content,
        )
        self.assertIn(
            "pocket-id:v2.14.0@"
            "sha256:01540977dcf4c7b41b1159f34d68e463"
            "2f2658d62790e460ca65a42722b13c4a",
            content,
        )

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
        for phase in (
            "post-accounting",
            "post-source-restoration",
            "post-target-configuration",
            "final-reconstruction",
        ):
            self.assertIn(
                f"scripts/migration-outbound-safety {phase}",
                target,
            )
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

    def test_tempfile_templates_are_portable_to_bsd_mktemp(self):
        for relative in (
            "scripts/qa-seed",
            "scripts/qa-environment",
            "scripts/production-cutover",
        ):
            with self.subTest(script=relative):
                script = (ROOT / relative).read_text(encoding="utf-8")
                self.assertNotIn("XXXXXX.json", script)
                self.assertNotIn("XXXXXX.tsv", script)

    def test_qa_persona_bootstrap_cannot_race_product_crons(self):
        qa = QA_SCRIPT.read_text(encoding="utf-8")
        start = qa.index("b2c_qa_bootstrap() {")
        function = qa[start : qa.index("\nusl_cli_title", start)]

        self.assertIn('"${compose[@]}" stop odoo', function)
        self.assertIn("--profile init run --rm -T --no-deps", function)
        self.assertIn('"${compose[@]}" up -d --wait odoo', function)
        self.assertLess(
            function.index('"${compose[@]}" stop odoo'),
            function.index("b2c_qa_bootstrap.py"),
        )
        self.assertGreater(
            function.index('"${compose[@]}" up -d --wait odoo'),
            function.index("b2c_qa_bootstrap.py"),
        )

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
        self.assertIn("/usr/src/paperless/export/candidate", cutover)
        self.assertIn("PaperlessTask.objects.count()", cutover)
        self.assertIn("--user paperless --entrypoint python paperless-webserver", cutover)
        self.assertIn("manage.py document_importer --no-progress-bar", cutover)
        self.assertIn("mkdir -p '/target/filestore/$database' /target/sessions", cutover)
        self.assertIn("chown -R 1000:1000 /target", cutover)
        self.assertIn('--jobs="$RESTORE_JOBS"', cutover)
        self.assertIn("USL_PRODUCTION_CRON_ALLOWLIST_JSON", cutover)
        self.assertIn("journeys --evidence", cutover)


if __name__ == "__main__":
    unittest.main()
