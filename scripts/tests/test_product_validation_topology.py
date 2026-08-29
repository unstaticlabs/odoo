from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).parents[2]


class ProductValidationTopologyTest(unittest.TestCase):
    def test_boundaries_prefer_running_exact_odoo_and_never_start_dependencies(self):
        for relative in (
            "scripts/accounting-restore",
            "scripts/b2c-restore",
            "scripts/project-restore",
            "scripts/tese-restore",
            "scripts/platform-billing-restore",
        ):
            with self.subTest(relative=relative):
                script = (ROOT / relative).read_text(encoding="utf-8")
                start = script.index("validate_product_boundary() {")
                function = script[start : script.index("\n}", start) + 2]
                self.assertIn("ps --status running -q odoo", function)
                self.assertIn("exec -T odoo", function)
                self.assertIn("run --rm -T --no-deps", function)

    def test_read_only_product_validation_does_not_stop_runtime_odoo(self):
        for relative in (
            "scripts/b2c-restore",
            "scripts/project-restore",
            "scripts/tese-restore",
            "scripts/platform-billing-restore",
        ):
            with self.subTest(relative=relative):
                script = (ROOT / relative).read_text(encoding="utf-8")
                stop = script.index("stop odoo")
                guard = script.rfind('[[ "$command_name" != product-validate ]]', 0, stop)
                self.assertGreaterEqual(guard, 0)

    def test_collaboration_boundary_cannot_reconcile_database_dependency(self):
        script = (ROOT / "scripts/collaboration-restore").read_text(encoding="utf-8")
        start = script.index("product_validate() {")
        function = script[start : script.index("\n}", start) + 2]
        self.assertIn("ps --status running -q odoo", function)
        self.assertIn("docker cp", function)
        self.assertIn("docker exec -i", function)
        self.assertNotIn("docker compose", function[function.index('if [[ -z "$container"') :])

    def test_stopped_one_off_evidence_does_not_block_read_only_boundary(self):
        script = (ROOT / "scripts/platform-billing-restore").read_text(encoding="utf-8")
        self.assertIn('[[ -z "$working_dir_label" && "$running" == false ]]', script)


if __name__ == "__main__":
    unittest.main()
