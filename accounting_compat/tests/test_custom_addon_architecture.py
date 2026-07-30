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

    def test_compatibility_module_uses_foundation_and_expense_feature(self):
        manifests = _manifests()
        dependencies = set(manifests["rebuild_account_migration"]["depends"])

        self.assertIn("usl_accounting", dependencies)
        self.assertIn("usl_expense_batch", dependencies)
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


if __name__ == "__main__":
    unittest.main()
