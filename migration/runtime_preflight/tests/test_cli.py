import unittest

from migration.runtime_preflight.cli import GIB, evaluate_capacity


class RuntimePreflightCase(unittest.TestCase):
    def test_low_memory_shared_runtime_is_blocked(self):
        decision = evaluate_capacity(
            total_memory=8 * GIB,
            current_project="migration",
            running_projects={"migration", "paperless", "b2c"},
            production=False,
            allow_concurrent=False,
        )

        self.assertFalse(decision.accepted)
        self.assertIn("OOM-killed", decision.reason)

    def test_low_memory_dedicated_development_runtime_is_allowed(self):
        decision = evaluate_capacity(
            total_memory=8 * GIB,
            current_project="migration",
            running_projects={"migration"},
            production=False,
            allow_concurrent=False,
        )

        self.assertTrue(decision.accepted)

    def test_explicit_development_override_is_supported(self):
        decision = evaluate_capacity(
            total_memory=8 * GIB,
            current_project="migration",
            running_projects={"migration", "paperless"},
            production=False,
            allow_concurrent=True,
        )

        self.assertTrue(decision.accepted)

    def test_production_rejects_foreign_projects_at_any_capacity(self):
        decision = evaluate_capacity(
            total_memory=64 * GIB,
            current_project="migration",
            running_projects={"migration", "unrelated"},
            production=True,
            allow_concurrent=False,
        )

        self.assertFalse(decision.accepted)
        self.assertIn("dedicated Docker runtime", decision.reason)


if __name__ == "__main__":
    unittest.main()
