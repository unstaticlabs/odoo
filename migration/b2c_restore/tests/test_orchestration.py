import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TARGET_RECONSTRUCT = ROOT / "scripts/target-reconstruct"


class B2cOrchestrationTest(unittest.TestCase):
    def test_document_links_are_refreshed_before_finalization(self):
        script = TARGET_RECONSTRUCT.read_text(encoding="utf-8")

        initial_b2c = script.index('run_stage "restore B2C commerce evidence"')
        documents = script.index('run_stage "restore Documents archive"')
        refresh = script.index('run_stage "refresh B2C Documents links"')
        finalization = script.index('run_stage "finalize migration boundary"')

        self.assertLess(initial_b2c, documents)
        self.assertLess(documents, refresh)
        self.assertLess(refresh, finalization)
        self.assertNotIn("-u all", script)


if __name__ == "__main__":
    unittest.main()
