"""The product-module set has one definition and every former copy reads it."""

from __future__ import annotations

import ast
import json
import re
import subprocess
import unittest
from pathlib import Path

from operations.mattermost_odoo_release import load_pin
from operations.module_release import (
    build_inventory,
    external_product_modules,
    installable_manifests,
    product_modules,
)

ROOT = Path(__file__).resolve().parents[2]
SURFACE = ROOT / "custom-addons/usl_access_control/policy/action_surface.json"

#: Files that used to carry their own copy of the set. A literal list of
#: product modules reappearing in any of them is drift waiting to happen.
FORMER_PYTHON_COPIES = (
    "operations/release_identity.py",
    "scripts/action_risk_inventory.py",
    "scripts/odoo/product_database_boundary.py",
    "scripts/odoo/release_identity.py",
    "migration/documents_archive/release_identity.py",
)
FORMER_SHELL_COPIES = (
    "migration/internal/finalize",
    "scripts/odoo-dev",
    "scripts/ci-product-database",
)
#: Odoo shell scripts cannot import operations/, so their callers hand them
#: the set through this variable.
RECEIVERS = (
    "scripts/odoo/product_database_boundary.py",
    "scripts/odoo/release_identity.py",
)
CALLERS = (
    "scripts/check-product-database-boundary",
    "scripts/ci-product-database",
    "migration/internal/candidate",
    "migration/internal/cutover",
)


def _string_literals(node: ast.AST) -> list[str] | None:
    """Strings a literal names: a set/tuple/list of strings, or one CSV string."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [item.strip() for item in node.value.split(",")]
    if not isinstance(node, (ast.Set, ast.Tuple, ast.List)):
        return None
    values = [item.value for item in node.elts if isinstance(item, ast.Constant)]
    if not all(isinstance(v, str) for v in values):
        return None
    return values


class ProductModuleRegistryTest(unittest.TestCase):
    def test_the_set_is_the_manifests_plus_the_external_pin(self) -> None:
        modules = product_modules(ROOT)
        manifests = {name for name, _ in installable_manifests(ROOT)}
        pinned = load_pin(ROOT)["module"]

        self.assertEqual(external_product_modules(ROOT), {pinned})
        self.assertEqual(modules, manifests | {pinned})
        self.assertIn("usl_document_templates", modules)
        self.assertIn("usl_mattermost", modules)
        # The synthetic fixture lives outside custom-addons/ and needs no exclusion.
        self.assertNotIn("usl_bootstrap", modules)
        self.assertFalse((ROOT / "custom-addons/usl_bootstrap").exists())
        self.assertTrue((ROOT / "test-addons/usl_bootstrap/__manifest__.py").is_file())
        # The release inventory describes the same add-ons, external export aside.
        self.assertEqual(set(build_inventory(ROOT)["modules"]) | {pinned}, modules)

    def test_the_shell_view_prints_the_same_set(self) -> None:
        output = subprocess.run(
            (ROOT / "scripts/product-modules",),
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        self.assertEqual(output, ",".join(sorted(product_modules(ROOT))))

    def test_the_sealed_surface_roots_are_the_product_set(self) -> None:
        surface = json.loads(SURFACE.read_text(encoding="utf-8"))
        self.assertEqual(surface["root_modules"], sorted(product_modules(ROOT)))

    def test_no_former_copy_hard_codes_the_set_again(self) -> None:
        modules = product_modules(ROOT)
        for relative in FORMER_PYTHON_COPIES:
            tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                values = _string_literals(node)
                if values is None:
                    continue
                named = modules & set(values)
                self.assertLess(
                    len(named),
                    3,
                    f"{relative} hard-codes product modules again: {sorted(named)}",
                )
        # Three or more product-module names separated only by commas, quotes
        # or whitespace is a list, whatever the shell syntax around it.
        names = "|".join(sorted(map(re.escape, modules)))
        listing = re.compile(rf"(?:\b(?:{names})\b[,\s'\"]+){{2}}\b(?:{names})\b")
        for relative in FORMER_SHELL_COPIES:
            text = (ROOT / relative).read_text(encoding="utf-8")
            match = listing.search(text)
            self.assertIsNone(
                match,
                f"{relative} hard-codes product modules again: {match and match.group(0)!r}",
            )

    def test_odoo_shell_scripts_receive_the_set_from_their_callers(self) -> None:
        for relative in RECEIVERS:
            text = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn('os.environ.get("USL_PRODUCT_MODULES"', text, relative)
            self.assertIn("received_product_modules()", text, relative)
        for relative in CALLERS:
            text = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn("scripts/product-modules", text, relative)
            self.assertIn("USL_PRODUCT_MODULES=", text, relative)


if __name__ == "__main__":
    unittest.main()
