import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SIGN_RESTORE = ROOT / "scripts/sign-restore"
TARGET_RECONSTRUCT = ROOT / "scripts/target-reconstruct"


class SignOrchestrationTest(unittest.TestCase):
    def test_canonical_reconstruction_uses_governed_migration_project(self):
        restore = SIGN_RESTORE.read_text(encoding="utf-8")
        reconstruction = TARGET_RECONSTRUCT.read_text(encoding="utf-8")

        self.assertIn("usl-odoo-migration-?*", restore)
        self.assertIn('if [[ "$canonical_target" != 1 ]]', restore)
        self.assertIn(
            'usl_verify_compose_scope "$compose_project" "$ROOT" "Sign restoration"',
            restore,
        )
        self.assertIn("SIGN_CANONICAL_TARGET=1", reconstruction)
        self.assertIn("SIGN_TARGET_DATABASE=odoo_dev", reconstruction)

    def test_standalone_restore_families_remain_isolated(self):
        restore = SIGN_RESTORE.read_text(encoding="utf-8")

        self.assertIn("codex-migration-?*|usl-migration-?*", restore)
        self.assertIn("Refusing non-isolated Sign migration project", restore)
        self.assertIn(
            "Refusing non-canonical Sign target in governed reconstruction project",
            restore,
        )

    def test_local_sign_restore_uses_shared_ollama_runtime_selection(self):
        restore = SIGN_RESTORE.read_text(encoding="utf-8")

        self.assertIn('source "$ROOT/scripts/lib/ollama-runtime.sh"', restore)
        self.assertIn('usl_prepare_ollama_runtime "$ROOT"', restore)
        self.assertIn('if [[ -n "$USL_OLLAMA_COMPOSE_OVERRIDE" ]]', restore)
        self.assertIn(
            'export COMPOSE_FILE="${COMPOSE_FILE:-$ROOT/compose.yaml}:'
            '$USL_OLLAMA_COMPOSE_OVERRIDE"',
            restore,
        )
        self.assertNotIn('compose+=(-f "$USL_OLLAMA_COMPOSE_OVERRIDE")', restore)

    def test_canonical_restore_uses_project_pocket_id_environment(self):
        restore = SIGN_RESTORE.read_text(encoding="utf-8")

        self.assertIn(
            'canonical_env_file="${POCKET_ID_ENV_FILE:-$ROOT/.pocket-id.env}"',
            restore,
        )
        self.assertIn(
            'POCKET_ID_ENV_FILE="$canonical_env_file" '
            "scripts/pocket-id-dev bootstrap",
            restore,
        )
        self.assertIn(
            'if [[ "$COMPOSE_PROJECT_NAME" != "$requested_compose_project" ]]',
            restore,
        )
        self.assertIn('compose+=(--env-file "$canonical_env_file")', restore)

    def test_validation_derives_composite_counts_from_the_frozen_source(self):
        validation = (
            ROOT / "migration/sign_restore/scripts/validate_restore.py"
        ).read_text(encoding="utf-8")

        self.assertIn("inactive_template_documents", validation)
        self.assertIn('sha256(reader.binary(row))', validation)
        self.assertIn(
            'len(source["requests"]) + len(source["messages"]) + '
            'len(source["logs"])',
            validation,
        )
        self.assertNotIn("len(external_documents) == 41", validation)
        self.assertNotIn('counts["mail.message"] == 86', validation)

    def test_restore_rebinds_exact_finalized_business_records(self):
        restore = (
            ROOT / "migration/sign_restore/scripts/run_restore.py"
        ).read_text(encoding="utf-8")
        validation = (
            ROOT / "migration/sign_restore/scripts/validate_restore.py"
        ).read_text(encoding="utf-8")

        self.assertIn("finalized_candidates", restore)
        self.assertIn(
            '("original_sha256", "=", artifact_match["signed_sha256"])',
            restore,
        )
        self.assertIn("Ambiguous finalized request", restore)
        self.assertIn("Ambiguous finalized history message", restore)
        self.assertIn("changed business identity", restore)
        self.assertIn("expected_archives", restore)
        self.assertIn("changed {field_name}", restore)
        self.assertIn("usl_sign_transition=INTERNAL_OPERATION", restore)
        self.assertIn(') == len(source["requests"])', validation)
        self.assertIn(') == len(source["signers"])', validation)

    def test_scoped_paperless_identity_is_removed_on_success_and_failure(self):
        restore = SIGN_RESTORE.read_text(encoding="utf-8")

        self.assertIn("deprovision_archive_identity()", restore)
        self.assertIn("cleanup_sign_restore()", restore)
        self.assertIn('trap cleanup_sign_restore EXIT', restore)
        self.assertIn("paperless_migration_access_cleanup.py", restore)


if __name__ == "__main__":
    unittest.main()
