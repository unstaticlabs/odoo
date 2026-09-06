"""A stage must mean the same thing locally and against a deployed database."""

import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LIBRARY = ROOT / "migration" / "internal" / "lib" / "b2c-stages.sh"
LOCAL_RUNNER = ROOT / "migration" / "internal" / "b2c-restore"
DEPLOYED_RUNNER = ROOT / "migration" / "internal" / "b2c-restore-deployed"

SCRIPT_STAGES = (
    "import",
    "catalog",
    "native-history",
    "native-history-dry-run",
    "reclassify-marketing",
    "validate",
    "finalize",
)


def _resolve(function, stage):
    completed = subprocess.run(
        ["bash", "-c", f'source "$1"; {function} "$2"', "--", str(LIBRARY), stage],
        capture_output=True, text=True, check=False, cwd=ROOT,
    )
    return completed.returncode, completed.stdout


class TestSharedStages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.local = LOCAL_RUNNER.read_text()
        cls.deployed = DEPLOYED_RUNNER.read_text()

    def test_every_stage_resolves_to_a_script_that_exists(self):
        for stage in SCRIPT_STAGES:
            with self.subTest(stage=stage):
                code, path = _resolve("usl_b2c_stage_script", stage)
                self.assertEqual(code, 0, f"{stage} resolves to nothing")
                self.assertTrue((ROOT / path).is_file(), f"{stage} -> {path} is missing")

    def test_the_local_runner_runs_the_same_script_for_each_stage(self):
        """The two runners drift silently unless something compares them."""
        for stage in SCRIPT_STAGES:
            with self.subTest(stage=stage):
                _, path = _resolve("usl_b2c_stage_script", stage)
                self.assertIn(
                    path, self.local,
                    f"{stage} resolves to {path}, which the local runner never runs",
                )

    def test_an_unknown_stage_resolves_to_nothing(self):
        code, _ = _resolve("usl_b2c_stage_script", "drop-everything")
        self.assertNotEqual(code, 0)

    def test_the_history_mode_matches_the_stage_name(self):
        self.assertEqual(_resolve("usl_b2c_stage_history_mode", "native-history")[1], "apply")
        self.assertEqual(
            _resolve("usl_b2c_stage_history_mode", "native-history-dry-run")[1], "dry_run",
        )
        self.assertEqual(_resolve("usl_b2c_stage_history_mode", "catalog")[1], "")

    def test_the_deployed_runner_refuses_the_stages_that_drop_databases(self):
        """`test` builds and drops databases; `all` hides a chain behind one word."""
        self.assertIn("Refusing '$stage' against a deployed database", self.deployed)
        for absent in ("cleanup_test_database", "dropdb", "test_parsers"):
            self.assertNotIn(absent, self.deployed, f"{absent} must not reach a live database")

    def test_the_deployed_runner_requires_its_confirmation_and_backup(self):
        for required in (
            "B2C_TARGET_CONFIRM",
            "B2C_BACKUP_RECEIPT",
            "verify_backup_receipt.py",
            "USL_MIGRATION_SOURCE_SHA256",
        ):
            self.assertIn(required, self.deployed)


if __name__ == "__main__":
    unittest.main()
