import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TARGET_RECONSTRUCT = ROOT / "scripts/target-reconstruct"
B2C_RESTORE = ROOT / "scripts/b2c-restore"


class B2cOrchestrationTest(unittest.TestCase):
    def test_document_links_are_refreshed_before_finalization(self):
        script = TARGET_RECONSTRUCT.read_text(encoding="utf-8")

        initial_b2c = script.index('run_stage "restore B2C commerce evidence"')
        documents = script.index('run_stage "restore Documents archive"')
        refresh = script.index(
            'run_stage "finalize B2C relationships and Documents links"',
        )
        finalization = script.index('run_stage "finalize migration boundary"')

        self.assertLess(initial_b2c, documents)
        self.assertLess(documents, refresh)
        self.assertLess(refresh, finalization)
        self.assertNotIn("-u all", script)
        self.assertIn("B2C_REQUIRE_FINAL_RELATIONSHIPS=1", script)

    def test_restore_accepts_owned_distribution_projects(self):
        script = B2C_RESTORE.read_text(encoding="utf-8")

        self.assertIn(
            'usl_verify_compose_scope "$compose_project" "$ROOT" "B2C restoration"',
            script,
        )
        self.assertIn("usl-odoo-*|usl-migration-?*|codex-migration-?*)", script)
        self.assertIn("Refusing protected B2C target", script)
        self.assertIn(
            'source_dump_dir="${USL_ONLINE_DUMP_DIR:-$ROOT/usl-online-dump}"',
            script,
        )
        self.assertNotIn("/Users/", script)
        self.assertIn(
            'supplemental_dir="$source_dump_dir/supplemental/b2c"',
            script,
        )
        self.assertNotIn("artifacts/b2c-restore/source", script)
        self.assertIn("scripts/accounting-compat source-restore", script)


if __name__ == "__main__":
    unittest.main()
