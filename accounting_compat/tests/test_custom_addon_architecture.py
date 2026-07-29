from __future__ import annotations

import ast
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

    def test_foundation_does_not_take_ownership_of_new_models(self):
        named_models = []
        for path in (CUSTOM_ADDONS / "usl_accounting" / "models").glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                    continue
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if any(
                    isinstance(target, ast.Name) and target.id == "_name"
                    for target in targets
                ):
                    named_models.append(path.name)

        self.assertEqual(
            named_models,
            [],
            "foundation extraction must not reassign generated model XML IDs",
        )

    def test_obsolete_placeholder_is_absent(self):
        self.assertNotIn("usl_custom_placeholder", _manifests())


if __name__ == "__main__":
    unittest.main()
