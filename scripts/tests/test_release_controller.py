from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from operations.release_controller import STAGES, ReleaseControllerError, create, load, run


class ReleaseControllerTests(unittest.TestCase):
    def handlers(self, fail=None, calls=None):
        calls = calls if calls is not None else []

        def handler(stage):
            def execute():
                calls.append(stage)
                if stage == fail:
                    raise RuntimeError("injected")
                return {"stage": stage}
            return execute

        return {stage: handler(stage) for stage in STAGES}

    def test_resumes_without_repeating_completed_stages(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            create(path, run_id="run", target="staging", release="a" * 64)
            run(path, self.handlers(), stop_after="candidate-upgrade")
            calls = []
            result = run(path, self.handlers(calls=calls))
            self.assertEqual(result["status"], "admitted")
            self.assertNotIn("resolve", calls)
            self.assertEqual(calls[-1], "record")

    def test_failure_injection_at_every_stage_records_safe_recovery(self):
        for failed_stage in STAGES:
            with self.subTest(stage=failed_stage), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "state.json"
                create(path, run_id="run", target="production", release="a" * 64)
                with self.assertRaisesRegex(RuntimeError, "injected"):
                    run(path, self.handlers(fail=failed_stage))
                state = load(path)
                expected = "forward-fix-only" if STAGES.index(failed_stage) > STAGES.index("reopen") else "rollback-previous-generation"
                self.assertEqual(state["recovery"], expected)

    def test_checksum_tampering_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            create(path, run_id="run", target="staging", release="a" * 64)
            path.write_text(path.read_text().replace('"staging"', '"production"'), encoding="utf-8")
            with self.assertRaisesRegex(ReleaseControllerError, "checksum"):
                load(path)


if __name__ == "__main__":
    unittest.main()
