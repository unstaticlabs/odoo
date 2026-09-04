from __future__ import annotations

import hashlib
import json
import unittest

from operations.qualification_plan import affected_modules


def inventory() -> dict:
    modules = {
        "foundation": {
            "version": "1.0",
            "dependencies": [],
            "source_sha256": "a" * 64,
            "stored_model_sha256": "b" * 64,
        },
        "dependent": {
            "version": "1.0",
            "dependencies": ["foundation"],
            "source_sha256": "c" * 64,
            "stored_model_sha256": "d" * 64,
        },
        "unrelated": {
            "version": "1.0",
            "dependencies": [],
            "source_sha256": "e" * 64,
            "stored_model_sha256": "f" * 64,
        },
    }
    canonical = json.dumps(modules, sort_keys=True, separators=(",", ":"))
    return {
        "schema": "usl-module-inventory/v1",
        "modules": modules,
        "sha256": hashlib.sha256(canonical.encode()).hexdigest(),
    }


class QualificationPlanTests(unittest.TestCase):
    def test_changed_module_includes_reverse_dependencies(self):
        self.assertEqual(
            affected_modules(["custom-addons/foundation/models/item.py"], inventory()),
            ["dependent", "foundation"],
        )

    def test_changed_dependent_does_not_select_dependency(self):
        self.assertEqual(
            affected_modules(["custom-addons/dependent/views/item.xml"], inventory()),
            ["dependent"],
        )

    def test_foundation_change_selects_every_module(self):
        self.assertEqual(
            affected_modules(["odoo/orm/models.py"], inventory()),
            ["dependent", "foundation", "unrelated"],
        )

    def test_non_runtime_change_selects_no_module(self):
        self.assertEqual(affected_modules(["docs/operations/readme.md"], inventory()), [])


if __name__ == "__main__":
    unittest.main()
