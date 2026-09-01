from __future__ import annotations

import ast
import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "release_identity",
    ROOT / "operations/release_identity.py",
)
RELEASE_IDENTITY = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(RELEASE_IDENTITY)


class ReleaseIdentityTest(unittest.TestCase):
    def test_product_perimeter_has_versioned_runtime_modules_only(self):
        versions = RELEASE_IDENTITY.product_module_versions()
        self.assertEqual(set(versions), RELEASE_IDENTITY.PRODUCT_MODULES)
        self.assertNotIn("usl_bootstrap", versions)
        self.assertTrue(all(version.startswith("saas~19.3.") for version in versions.values()))

    def test_distribution_image_embeds_runtime_inputs(self):
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        distribution = dockerfile.split("FROM base AS product", 1)[1].split(
            "FROM base AS test", 1
        )[0]
        dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
        preprod = (ROOT / "compose.production.yaml").read_text(encoding="utf-8")
        self.assertIn("com.unstaticlabs.odoo.component-input-sha256", distribution)
        self.assertIn("USL_COMPONENT_INPUT_SHA256", distribution)
        self.assertIn("USL_OCA_BUNDLE_SHA256", distribution)
        self.assertIn("custom-addons ./custom-addons", distribution)
        self.assertIn("/srv/resolved ./oca-addons", distribution)
        self.assertIn("docs/users ./docs/users", distribution)
        self.assertIn('VOLUME ["/var/lib/odoo"]', dockerfile)
        self.assertIn("!custom-addons/**", dockerignore)
        self.assertIn("!oca-src/**", dockerignore)
        self.assertIn("build: !reset null", preprod)
        self.assertNotIn("./custom-addons", preprod)
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
        self.assertIn("USL_RELEASE_COMMIT: ${USL_RELEASE_COMMIT:-development}", compose)

    def test_release_and_database_boundary_scripts_parse(self):
        for relative_path in (
            "operations/release_identity.py",
            "scripts/odoo/documents_identity_boundary.py",
            "scripts/odoo/documents_integrity_manifest.py",
            "scripts/odoo/documents_restore_acceptance.py",
            "scripts/odoo/release_identity.py",
            "scripts/odoo/product_database_boundary.py",
        ):
            ast.parse((ROOT / relative_path).read_text(encoding="utf-8"))

    def test_database_identity_uses_saas_19_typed_parameters(self):
        identity_script = (ROOT / "scripts/odoo/release_identity.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('params.get_str("database.uuid")', identity_script)
        self.assertIn('params.set_str("usl.release.identity"', identity_script)
        self.assertNotIn("get_param", identity_script)
        self.assertNotIn("set_param", identity_script)

    def test_database_boundary_parameterizes_sql_wildcards(self):
        boundary_script = (ROOT / "scripts/odoo/product_database_boundary.py").read_text(
            encoding="utf-8"
        )
        self.assertEqual(boundary_script.count("LIKE %s"), 2)
        self.assertEqual(boundary_script.count('(\"rebuild_source_%\",)'), 2)

    def test_schema_finalizer_removes_inherited_field_residue(self):
        finalizer = (
            ROOT / "migration/accounting_restore/scripts/finalize_schema.py"
        ).read_text(encoding="utf-8")
        self.assertIn("for model_name in env.registry.models", finalizer)
        self.assertIn("metadata.with_context(force_delete=True).unlink()", finalizer)
        self.assertIn("removed_migration_field_metadata", finalizer)
        self.assertNotIn("PRODUCT_MODELS", finalizer)


if __name__ == "__main__":
    unittest.main()
