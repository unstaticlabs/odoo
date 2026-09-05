from __future__ import annotations

import copy
import unittest
import tempfile
from pathlib import Path

from operations.module_release import (
    ModuleReleaseError,
    build_inventory,
    derive_legacy_upgrade_plan,
    derive_upgrade_plan,
    validate_upgrade_plan,
)


def inventory(modules):
    import hashlib
    import json

    body = {
        name: {
            "version": version,
            "dependencies": dependencies,
            "source_sha256": source * 64,
            "stored_model_sha256": model * 64,
        }
        for name, version, dependencies, source, model in modules
    }
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return {
        "schema": "usl-module-inventory/v1",
        "modules": body,
        "sha256": hashlib.sha256(canonical.encode()).hexdigest(),
    }


def release(identity, modules, foundation="f"):
    return {"identity": identity, "modules": modules, "foundation": {"digest": foundation * 64}}


class ModuleReleaseTests(unittest.TestCase):
    def setUp(self):
        self.active_inventory = inventory(
            [("base_product", "1.0", [], "a", "b"), ("dependent", "1.0", ["base_product"], "c", "d")]
        )

    def test_changed_module_includes_reverse_dependency_closure(self):
        candidate = inventory(
            [("base_product", "1.1", [], "e", "f"), ("dependent", "1.0", ["base_product"], "c", "d")]
        )
        plan = derive_upgrade_plan(
            release("active", self.active_inventory),
            release("candidate", candidate),
            {"base_product", "dependent"},
        )
        self.assertEqual(plan["upgrade_modules"], ["base_product", "dependent"])
        self.assertEqual(validate_upgrade_plan(plan), plan)

    def test_new_product_module_installs_without_enabling_existing_optional_module(self):
        active = inventory([("base", "1.0", [], "a", "b"), ("optional", "1.0", [], "c", "d")])
        candidate = inventory([("base", "1.0", [], "a", "b"), ("optional", "1.0", [], "c", "d"), ("new_feature", "1.0", ["base"], "e", "f")])
        result = derive_upgrade_plan(release("active", active), release("candidate", candidate), {"base"})
        self.assertEqual(result["installed_modules"], ["base"])
        self.assertEqual(result["upgrade_modules"], ["new_feature"])
        self.assertEqual(result["reasons"], {"new_feature": ["new-product-module"]})
        self.assertEqual(validate_upgrade_plan(result), result)
        result["reasons"]["new_feature"] = ["source-changed"]
        with self.assertRaisesRegex(ModuleReleaseError, "unapproved new"):
            validate_upgrade_plan(result)

    def test_changed_source_without_version_bump_still_upgrades_dependents(self):
        candidate = copy.deepcopy(self.active_inventory)
        candidate["modules"]["base_product"]["source_sha256"] = "e" * 64
        import hashlib, json
        candidate["sha256"] = hashlib.sha256(
            json.dumps(candidate["modules"], sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        plan = derive_upgrade_plan(
            release("active", self.active_inventory),
            release("candidate", candidate),
            {"base_product", "dependent"},
        )
        self.assertEqual(plan["upgrade_modules"], ["base_product", "dependent"])
        self.assertIn("source-changed", plan["reasons"]["base_product"])
        self.assertEqual(validate_upgrade_plan(plan), plan)

    def test_changed_model_at_same_version_is_included_in_upgrade(self):
        candidate = inventory(
            [("base_product", "1.0", [], "e", "f"), ("dependent", "1.0", ["base_product"], "c", "d")]
        )
        plan = derive_upgrade_plan(
            release("active", self.active_inventory), release("candidate", candidate),
            {"base_product", "dependent"},
        )
        self.assertEqual(plan["upgrade_modules"], ["base_product", "dependent"])
        self.assertIn("stored-model-changed", plan["reasons"]["base_product"])

    def test_foundation_change_upgrades_every_owned_installed_module(self):
        plan = derive_upgrade_plan(
            release("active", self.active_inventory, "a"),
            release("candidate", self.active_inventory, "b"),
            {"base_product", "dependent"},
        )
        self.assertEqual(plan["upgrade_modules"], ["base_product", "dependent"])

    def test_unknown_installed_module_fails_closed(self):
        with self.assertRaisesRegex(ModuleReleaseError, "ambiguous"):
            derive_upgrade_plan(
                release("active", self.active_inventory),
                release("candidate", self.active_inventory),
                {"foreign"},
            )

    def test_legacy_release_transition_upgrades_every_owned_module(self):
        candidate = release("candidate", self.active_inventory)
        plan = derive_legacy_upgrade_plan(
            candidate,
            {"base_product", "dependent"},
            active_identity="legacy-digest",
        )
        self.assertEqual(plan["upgrade_modules"], ["base_product", "dependent"])
        self.assertEqual(
            plan["reasons"]["base_product"],
            ["legacy-v2-release-has-no-module-inventory"],
        )
        self.assertEqual(validate_upgrade_plan(plan), plan)

    def test_inventory_ignores_python_and_test_cache_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            module = root / "custom-addons/product"
            module.mkdir(parents=True)
            (module / "__manifest__.py").write_text("{'version': '1.0', 'depends': []}")
            baseline = build_inventory(root)
            for relative in ("__pycache__/__manifest__.cpython-314.pyc", "models/__pycache__/model.pyc", ".pytest_cache/result", "stale.pyo"):
                cache = module / relative
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_bytes(b"generated locally")
            self.assertEqual(build_inventory(root), baseline)
            (module / "models/model.py").write_text("new_model = True")
            self.assertNotEqual(build_inventory(root), baseline)

    def test_release_inventory_rejects_missing_dependency(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            module = root / "custom-addons/product"
            module.mkdir(parents=True)
            (module / "__manifest__.py").write_text(
                "{'version': '1.0', 'depends': ['not_shipped']}\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ModuleReleaseError, "not_shipped"):
                build_inventory(root, require_dependencies=True)

    def test_release_inventory_accepts_pinned_symlink_dependency(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            module = root / "custom-addons/product"
            dependency = root / "oca-src/repository/dependency"
            linked = root / "oca-addons/dependency"
            module.mkdir(parents=True)
            dependency.mkdir(parents=True)
            linked.parent.mkdir(parents=True)
            linked.symlink_to(dependency)
            (module / "__manifest__.py").write_text(
                "{'version': '1.0', 'depends': ['dependency']}\n",
                encoding="utf-8",
            )
            (dependency / "__manifest__.py").write_text(
                "{'version': '1.0', 'depends': []}\n",
                encoding="utf-8",
            )
            value = build_inventory(root, require_dependencies=True)
            self.assertIn("product", value["modules"])


if __name__ == "__main__":
    unittest.main()
