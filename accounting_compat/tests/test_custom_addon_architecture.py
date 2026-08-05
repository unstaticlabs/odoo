from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CUSTOM_ADDONS = REPOSITORY_ROOT / "custom-addons"


def _manifests() -> dict[str, dict]:
    manifests = {}
    for path in CUSTOM_ADDONS.glob("*/__manifest__.py"):
        manifests[path.parent.name] = ast.literal_eval(
            path.read_text(encoding="utf-8"),
        )
    return manifests


class CustomAddonArchitectureTest(unittest.TestCase):
    def test_custom_dependency_graph_is_acyclic(self):
        manifests = _manifests()
        custom_names = set(manifests)
        graph = {
            name: set(manifest.get("depends", ())) & custom_names
            for name, manifest in manifests.items()
        }
        visiting = set()
        visited = set()

        def visit(name, path):
            self.assertNotIn(
                name,
                visiting,
                f"custom add-on dependency cycle: {' -> '.join((*path, name))}",
            )
            if name in visited:
                return
            visiting.add(name)
            for dependency in sorted(graph[name]):
                visit(dependency, (*path, name))
            visiting.remove(name)
            visited.add(name)

        for module_name in sorted(graph):
            visit(module_name, ())

    def test_runtime_graph_excludes_test_bootstrap(self):
        manifests = _manifests()
        reverse_dependencies = {
            name
            for name, manifest in manifests.items()
            if "usl_bootstrap" in manifest.get("depends", ())
        }

        self.assertEqual(reverse_dependencies, set())
        self.assertNotIn("usl_bootstrap", manifests["rebuild_account_migration"]["depends"])

    def test_compatibility_module_uses_integrated_product_features(self):
        manifests = _manifests()
        dependencies = set(manifests["rebuild_account_migration"]["depends"])

        self.assertIn("usl_accounting", dependencies)
        self.assertIn("usl_expense_batch", dependencies)
        self.assertIn("usl_platform_billing", dependencies)
        self.assertIn("usl_platform_billing_pocketid", dependencies)
        self.assertEqual(
            set(manifests["usl_platform_billing_pocketid"]["depends"]),
            {"usl_platform_billing", "usl_pocketid"},
        )
        self.assertNotIn(
            "rebuild_account_migration",
            manifests["usl_platform_billing"].get("depends", ()),
        )
        self.assertNotIn(
            "rebuild_account_migration",
            manifests["usl_accounting"].get("depends", ()),
        )

    def test_foundation_owns_only_documented_runtime_models(self):
        named_models = {}
        for path in (CUSTOM_ADDONS / "usl_accounting" / "models").glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Assign):
                    continue
                if any(
                    isinstance(target, ast.Name) and target.id == "_name"
                    for target in node.targets
                ) and isinstance(node.value, ast.Constant):
                    named_models.setdefault(path.name, []).append(node.value.value)

        self.assertEqual(
            named_models,
            {
                "expense_bank_matching.py": [
                    "usl.expense.bank.match.candidate",
                ],
                "immediate_settlement.py": [
                    "account.immediate.settlement",
                    "account.immediate.settlement.allocation",
                ],
            },
            "foundation model ownership must stay runtime-only and documented",
        )

    def test_payment_settlement_assets_belong_to_foundation(self):
        manifests = _manifests()
        foundation_assets = manifests["usl_accounting"]["assets"]
        compatibility_assets = manifests["rebuild_account_migration"]["assets"]
        foundation_paths = {
            path
            for paths in foundation_assets.values()
            for path in paths
        }
        compatibility_paths = {
            path
            for paths in compatibility_assets.values()
            for path in paths
        }

        self.assertTrue(
            any("payment_suggestions" in path for path in foundation_paths),
        )
        self.assertTrue(
            any("immediate_settlement" in path for path in foundation_paths),
        )
        self.assertFalse(
            any(
                "payment_suggestions" in path or "immediate_settlement" in path
                for path in compatibility_paths
            ),
            "new settlement behavior must not return to the compatibility module",
        )

    def test_day_first_dates_are_owned_by_the_shared_locale_foundation(self):
        manifests = _manifests()
        locale_manifest = manifests["usl_locale"]

        self.assertTrue(locale_manifest["auto_install"])
        self.assertIn("web", locale_manifest["depends"])
        self.assertIn("usl_locale", manifests["usl_accounting"]["depends"])
        self.assertIn("usl_locale", manifests["usl_documents"]["depends"])

        locale_data = (
            CUSTOM_ADDONS / "usl_locale" / "data" / "european_date_format.xml"
        ).read_text(encoding="utf-8")
        self.assertEqual(locale_data.count("%d/%m/%Y"), 2)

        compatibility_manifest = manifests["rebuild_account_migration"]
        compatibility_paths = {
            path
            for key in ("data", "assets")
            for paths in (
                [compatibility_manifest.get(key, ())]
                if key == "data"
                else compatibility_manifest.get(key, {}).values()
            )
            for path in paths
        }
        self.assertFalse(
            any("european_date_format" in path for path in compatibility_paths),
            "the product-wide date convention must not return to migration compatibility",
        )

        forbidden_patterns = {
            "browser-native date input": re.compile(r"""\btype\s*=\s*["']date"""),
            "month-first placeholder": re.compile(
                r"MM/DD/YYYY|MM/dd/yyyy|%m/%d/%Y"
            ),
        }
        violations = []
        for suffix in ("*.js", "*.xml"):
            for path in CUSTOM_ADDONS.glob(f"*/static/src/**/{suffix}"):
                source = path.read_text(encoding="utf-8")
                for label, pattern in forbidden_patterns.items():
                    if pattern.search(source):
                        violations.append(
                            f"{path.relative_to(REPOSITORY_ROOT)}: {label}"
                        )
        self.assertEqual(
            violations,
            [],
            "product UI must use Odoo date components and DD/MM/YYYY: "
            + ", ".join(violations),
        )

    def test_obsolete_placeholder_is_absent(self):
        self.assertNotIn("usl_custom_placeholder", _manifests())

    def test_canonical_reconstruction_restores_source_and_finalizes_migration(self):
        script = (
            REPOSITORY_ROOT / "scripts" / "target-reconstruct"
        ).read_text(encoding="utf-8")
        ordered_steps = [
            "scripts/accounting-compat source-restore",
            "scripts/accounting-compat source-controls",
            "scripts/accounting-compat extract",
            "scripts/accounting-compat dev-reset",
            "scripts/accounting-compat dev-import",
            "scripts/accounting-compat dev-validate",
            "scripts/project-restore all",
            "scripts/tese-restore all",
            "scripts/accounting-restore finalize",
            "scripts/target-finalize",
        ]

        positions = [script.index(step) for step in ordered_steps]
        self.assertEqual(positions, sorted(positions))

    def test_project_restore_declares_temporary_accounting_dependency(self):
        manifest = ast.literal_eval(
            (
                REPOSITORY_ROOT
                / "migration/project_restore/addons/usl_project_restore/__manifest__.py"
            ).read_text(encoding="utf-8"),
        )
        self.assertIn("usl_accounting_restore", manifest["depends"])
        self.assertNotIn("rebuild_account_migration", manifest["depends"])

        project_script = (REPOSITORY_ROOT / "scripts/project-restore").read_text(
            encoding="utf-8",
        )
        self.assertIn("/mnt/accounting-migration-addons", project_script)

        project_traces = (
            REPOSITORY_ROOT
            / "migration/project_restore/addons/usl_project_restore/models/trace_models.py"
        ).read_text(encoding="utf-8")
        self.assertIn("usl.accounting.restore.source.mixin", project_traces)
        self.assertNotIn("rebuild.source.trace.mixin", project_traces)

    def test_tese_restore_declares_temporary_accounting_dependency(self):
        manifest = ast.literal_eval(
            (
                REPOSITORY_ROOT
                / "migration/tese_restore/addons/usl_tese_restore/__manifest__.py"
            ).read_text(encoding="utf-8"),
        )
        self.assertIn("usl_accounting_restore", manifest["depends"])
        self.assertNotIn("rebuild_account_migration", manifest["depends"])

        tese_script = (REPOSITORY_ROOT / "scripts/tese-restore").read_text(
            encoding="utf-8",
        )
        self.assertIn("/mnt/accounting-migration-addons", tese_script)
        self.assertIn("TESE_RESTORE_DEFER_PRODUCT_VALIDATE", tese_script)
        self.assertIn("require_reconstruction_schema", tese_script)
        self.assertIn(
            "This target is finalized; use a product-module upgrade instead.",
            tese_script,
        )
        recovery_script = (
            REPOSITORY_ROOT
            / "migration/tese_restore/scripts/recover_partial_finalized_target.py"
        ).read_text()
        self.assertIn("USL_TESE_RECOVER_PARTIAL_RERUN", recovery_script)
        self.assertIn("migration_modules.button_immediate_uninstall()", recovery_script)


if __name__ == "__main__":
    unittest.main()
