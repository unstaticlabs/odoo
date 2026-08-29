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


if __name__ == "__main__":
    unittest.main()
