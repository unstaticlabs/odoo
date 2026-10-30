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

    def _foundation_release(self, identity, *, core="a", oca="b", constraints="c", policy="d", modules=None):
        import hashlib
        import json

        result = release(identity, modules or self.active_inventory)
        parts = {
            "odoo_core_sha256": core * 64,
            "oca_sha256": oca * 64,
            "python_constraints_sha256": constraints * 64,
            "security_policy_sha256": policy * 64,
        }
        result["foundation"] = {
            **parts,
            "digest": hashlib.sha256(
                json.dumps(parts, sort_keys=True, separators=(",", ":")).encode(),
            ).hexdigest(),
        }
        return result

    def test_core_or_oca_content_change_updates_actual_installed_foundation(self):
        for changed_part, reason in (
            ("core", "odoo-core-changed"), ("oca", "oca-content-changed"),
        ):
            with self.subTest(changed_part=changed_part):
                plan = derive_upgrade_plan(
                    self._foundation_release("active"),
                    self._foundation_release("candidate", **{changed_part: "e"}),
                    {"base_product", "dependent"},
                    installed_foundation_modules={"base", "iap", "account_reconcile_oca"},
                )
                expected = ["account_reconcile_oca", "base", "base_product", "dependent", "iap"]
                self.assertEqual(plan["installed_modules"], expected)
                self.assertEqual(plan["upgrade_modules"], expected)
                self.assertEqual(plan["reasons"]["iap"], [reason])
                self.assertEqual(validate_upgrade_plan(plan), plan)

    def test_constraints_or_policy_change_does_not_reload_native_data(self):
        for changed_part in ("constraints", "policy"):
            with self.subTest(changed_part=changed_part):
                plan = derive_upgrade_plan(
                    self._foundation_release("active"),
                    self._foundation_release("candidate", **{changed_part: "e"}),
                    {"base_product", "dependent"},
                    installed_foundation_modules={"base", "iap"},
                )
                self.assertEqual(plan["upgrade_modules"], ["base_product", "dependent"])
                self.assertEqual(plan["installed_modules"], ["base", "base_product", "dependent", "iap"])
                self.assertEqual(plan["reasons"]["base_product"], ["foundation-changed"])

    def test_same_foundation_custom_edit_only_updates_affected_managed_closure(self):
        candidate = inventory([
            ("base_product", "1.0", [], "e", "b"),
            ("dependent", "1.0", ["base_product"], "c", "d"),
        ])
        plan = derive_upgrade_plan(
            self._foundation_release("active"),
            self._foundation_release("candidate", modules=candidate),
            {"base_product", "dependent"},
            installed_foundation_modules={"base", "iap"},
        )
        self.assertEqual(plan["upgrade_modules"], ["base_product", "dependent"])
        self.assertEqual(plan["installed_modules"], ["base", "base_product", "dependent", "iap"])

    def test_unchanged_release_records_foundation_without_scheduling_updates(self):
        plan = derive_upgrade_plan(
            self._foundation_release("active"),
            self._foundation_release("candidate"),
            {"base_product", "dependent"},
            installed_foundation_modules={"base", "iap"},
        )
        self.assertEqual(plan["upgrade_modules"], [])
        self.assertEqual(plan["installed_modules"], ["base", "base_product", "dependent", "iap"])
        self.assertEqual(validate_upgrade_plan(plan), plan)

    def test_native_update_never_enables_uninstalled_optional_modules(self):
        active = inventory([
            ("usl_product", "1.0", ["base"], "a", "b"),
            ("usl_optional", "1.0", ["iap"], "c", "d"),
        ])
        candidate = inventory([
            ("usl_product", "1.0", ["base"], "a", "b"),
            ("usl_optional", "1.0", ["iap"], "c", "d"),
            ("usl_new", "1.0", ["base"], "e", "f"),
        ])
        plan = derive_upgrade_plan(
            self._foundation_release("active", modules=active),
            self._foundation_release("candidate", core="e", modules=candidate),
            {"usl_product"},
            installed_foundation_modules={"base", "iap"},
        )
        self.assertEqual(plan["installed_modules"], ["base", "iap", "usl_product"])
        self.assertEqual(plan["upgrade_modules"], ["base", "iap", "usl_new", "usl_product"])
        self.assertEqual(plan["reasons"]["usl_new"], ["new-product-module"])
        self.assertNotIn("usl_optional", plan["upgrade_modules"])
        self.assertEqual(validate_upgrade_plan(plan), plan)

    def test_unknown_managed_module_is_not_hidden_by_foundation_input(self):
        with self.assertRaisesRegex(ModuleReleaseError, "ambiguous: foreign"):
            derive_upgrade_plan(
                self._foundation_release("active"),
                self._foundation_release("candidate", core="e"),
                {"foreign"}, installed_foundation_modules={"base", "foreign"},
            )

    def test_omitted_foundation_input_preserves_previous_plan_shape(self):
        plan = derive_upgrade_plan(
            self._foundation_release("active"),
            self._foundation_release("candidate", core="e"),
            {"base_product", "dependent"},
        )
        self.assertEqual(plan["installed_modules"], ["base_product", "dependent"])
        self.assertEqual(plan["upgrade_modules"], ["base_product", "dependent"])
        self.assertEqual(validate_upgrade_plan(plan), plan)

    def test_legacy_release_explicitly_updates_all_actual_installed_modules(self):
        plan = derive_legacy_upgrade_plan(
            self._foundation_release("candidate"), {"base_product", "dependent"},
            active_identity="legacy-digest",
            installed_foundation_modules={"base", "iap"},
        )
        self.assertEqual(plan["installed_modules"], ["base", "base_product", "dependent", "iap"])
        self.assertEqual(plan["upgrade_modules"], plan["installed_modules"])
        self.assertEqual(plan["reasons"]["iap"], ["legacy-v2-release-has-no-module-inventory"])
        self.assertEqual(validate_upgrade_plan(plan), plan)

    def test_native_module_list_is_covered_by_the_plan_digest(self):
        plan = derive_upgrade_plan(
            self._foundation_release("active"),
            self._foundation_release("candidate", core="e"),
            {"base_product", "dependent"}, installed_foundation_modules={"base", "iap"},
        )
        plan["upgrade_modules"].remove("iap")
        with self.assertRaisesRegex(ModuleReleaseError, "digest differs"):
            validate_upgrade_plan(plan)

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
