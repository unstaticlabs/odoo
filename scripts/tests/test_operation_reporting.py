from __future__ import annotations

import contextlib
import io
import unittest

from operations.stack import (
    CAPACITY_WARNING_BYTES,
    MINIMUM_FREE_BYTES,
    _capacity_detail,
    _report,
)


class OperationReportingTests(unittest.TestCase):
    def test_phase_reporting_uses_stderr_and_keeps_stdout_clean(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            _report("restore", "validation", "completed", "12.500s")
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(
            stderr.getvalue(),
            "usl-stack [restore] validation: completed: 12.500s\n",
        )

    def test_capacity_messages_have_actionable_severity(self) -> None:
        self.assertIn("below the 2 GiB safety floor", _capacity_detail(MINIMUM_FREE_BYTES - 1))
        self.assertIn("CRITICAL CAPACITY WARNING", _capacity_detail(CAPACITY_WARNING_BYTES - 1))
        self.assertEqual(_capacity_detail(10 * 1024**3), "10.0 GiB free")


if __name__ == "__main__":
    unittest.main()
