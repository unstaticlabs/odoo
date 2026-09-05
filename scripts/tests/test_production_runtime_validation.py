import ast
import datetime
import types
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "odoo" / "production_runtime_validation.py"


def load_stale_progress_function():
    module = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "stale_cron_progress"
    )
    namespace = {}
    exec(compile(ast.Module(body=[function], type_ignores=[]), SCRIPT, "exec"), namespace)
    return namespace["stale_cron_progress"]


def progress(record_id, cron_id, *, active=True, remaining=0, created_at=None):
    return types.SimpleNamespace(
        id=record_id,
        cron_id=types.SimpleNamespace(id=cron_id, active=active),
        remaining=remaining,
        create_date=created_at,
    )


class ProductionRuntimeValidationTest(unittest.TestCase):
    def test_completed_history_is_not_stale(self):
        now = datetime.datetime(2026, 9, 1, 4, 0)
        records = [
            progress(1, 10, remaining=5, created_at=now - datetime.timedelta(days=1)),
            progress(2, 10, remaining=0, created_at=now - datetime.timedelta(hours=3)),
            progress(3, 11, remaining=0, created_at=now - datetime.timedelta(days=1)),
        ]

        self.assertEqual(
            load_stale_progress_function()(records, now - datetime.timedelta(hours=2)),
            [],
        )

    def test_only_latest_active_unfinished_run_can_be_stale(self):
        now = datetime.datetime(2026, 9, 1, 4, 0)
        stale = progress(
            4,
            12,
            remaining=7,
            created_at=now - datetime.timedelta(hours=3),
        )
        records = [
            stale,
            progress(5, 13, active=False, remaining=2, created_at=now - datetime.timedelta(days=1)),
            progress(6, 14, remaining=4, created_at=now - datetime.timedelta(minutes=30)),
        ]

        self.assertEqual(
            load_stale_progress_function()(records, now - datetime.timedelta(hours=2)),
            [stale],
        )


if __name__ == "__main__":
    unittest.main()
