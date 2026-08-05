from __future__ import annotations

import ast
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "release_identity",
    ROOT / "scripts" / "release_identity.py",
)
RELEASE_IDENTITY = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(RELEASE_IDENTITY)


class ReleaseIdentityTest(unittest.TestCase):
    def test_product_perimeter_has_versioned_runtime_modules_only(self):
        versions = RELEASE_IDENTITY.product_module_versions()

        self.assertEqual(set(versions), RELEASE_IDENTITY.PRODUCT_MODULES)
        self.assertNotIn("usl_bootstrap", versions)
        self.assertTrue(
            all(version.startswith("saas~19.2.") for version in versions.values()),
        )

    def test_distribution_image_embeds_runtime_inputs(self):
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        distribution = dockerfile.split(
            "FROM base AS distribution",
            1,
        )[1].split("FROM base AS test", 1)[0]
        dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
        preprod = (ROOT / "compose.preprod.yaml").read_text(encoding="utf-8")

        self.assertIn("org.opencontainers.image.revision", distribution)
        self.assertIn("USL_OCA_BUNDLE_SHA256", distribution)
        self.assertIn("custom-addons /opt/usl/custom-addons", distribution)
        self.assertIn("oca-src /opt/usl/oca-src", distribution)
        self.assertIn("docs/users /opt/usl/user-docs", distribution)
        self.assertIn('VOLUME ["/var/lib/odoo"]', dockerfile)
        self.assertNotIn(
            'VOLUME ["/var/lib/odoo", "/mnt/custom-addons"]',
            dockerfile,
        )
        self.assertIn("!custom-addons/**", dockerignore)
        self.assertIn("!oca-src/**", dockerignore)
        self.assertIn("build: !reset null", preprod)
        self.assertIn("volumes: !override", preprod)
        self.assertNotIn("./custom-addons", preprod)

    def test_release_and_database_boundary_scripts_parse(self):
        for relative_path in (
            "scripts/release_identity.py",
            "scripts/odoo/documents_identity_boundary.py",
            "scripts/odoo/documents_integrity_manifest.py",
            "scripts/odoo/documents_restore_acceptance.py",
            "scripts/odoo/release_identity.py",
            "scripts/odoo/product_database_boundary.py",
        ):
            ast.parse((ROOT / relative_path).read_text(encoding="utf-8"))

    def test_database_identity_uses_saas_19_typed_parameters(self):
        identity_script = (
            ROOT / "scripts" / "odoo" / "release_identity.py"
        ).read_text(encoding="utf-8")

        self.assertIn('params.get_str("database.uuid")', identity_script)
        self.assertIn('params.set_str("usl.release.identity"', identity_script)
        self.assertNotIn("get_param", identity_script)
        self.assertNotIn("set_param", identity_script)

    def test_database_boundary_parameterizes_sql_wildcards(self):
        boundary_script = (
            ROOT / "scripts" / "odoo" / "product_database_boundary.py"
        ).read_text(encoding="utf-8")

        self.assertEqual(boundary_script.count('LIKE %s'), 2)
        self.assertEqual(boundary_script.count('(\"rebuild_source_%\",)'), 2)

    def test_schema_finalizer_removes_inherited_field_residue(self):
        finalizer = (
            ROOT / "migration" / "accounting_restore" / "scripts" / "finalize_schema.py"
        ).read_text(encoding="utf-8")

        self.assertIn("for model_name in env.registry.models", finalizer)
        self.assertIn("metadata.with_context(force_delete=True).unlink()", finalizer)
        self.assertIn("removed_migration_field_metadata", finalizer)
        self.assertNotIn("PRODUCT_MODELS", finalizer)

    def test_full_boundary_and_release_gate_include_database_state(self):
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        release = (ROOT / "scripts" / "preprod-release").read_text(
            encoding="utf-8",
        )
        boundary = (
            ROOT / "scripts" / "check-product-migration-boundary"
        ).read_text(encoding="utf-8")

        self.assertIn(
            "product-migration-boundary: product-migration-source-boundary",
            makefile,
        )
        self.assertIn("scripts/check-product-database-boundary", makefile)
        self.assertIn("USL_PRODUCT_BOUNDARY_PREPROD=1", release)
        self.assertIn("clean_install_product", release)
        self.assertIn("odoo_release_clean_install", release)
        self.assertIn("Pre-production clean installation: PASS", release)
        self.assertIn("finish-reconstruction", release)
        self.assertIn("finish_reconstruction", release)
        self.assertIn("finalize-reconstruction", release)
        self.assertIn("finalize_reconstruction", release)
        self.assertIn("DOCUMENTS_CANONICAL_RESET=1", release)
        self.assertIn("USL_RECONSTRUCT_REUSE_DOCUMENTS=0", release)
        self.assertIn("DOCUMENTS_REQUIRE_CHECKPOINT=0", release)
        self.assertIn("documents_identity_boundary", release)
        self.assertIn("documents-identity-boundary.json", release)
        self.assertIn("database_identity 0", release)
        self.assertIn("recovery-rehearsal", release)
        self.assertIn("scripts/documents-recovery-test preprod", release)
        self.assertIn("Pre-production release gate: PASS", release)
        self.assertLess(
            release.index("database_identity 0"),
            release.index("documents_identity_boundary \\\n    | tee"),
        )
        for module_name in (
            "usl_accounting_restore",
            "usl_identity_restore",
            "usl_product_restore",
            "usl_hr_restore",
            "usl_project_restore",
            "usl_tese_restore",
            "usl_platform_billing_restore",
        ):
            self.assertIn(module_name, boundary)

    def test_documents_direct_access_gate_is_fail_closed(self):
        boundary = (
            ROOT / "scripts" / "odoo" / "documents_identity_boundary.py"
        ).read_text(encoding="utf-8")

        self.assertIn("usl_documents.group_documents_manager", boundary)
        self.assertIn("usl_documents.group_documents_user", boundary)
        self.assertIn("usl_documents.group_documents_accountant", boundary)
        self.assertIn('mapping.sync_state != "synchronized"', boundary)
        self.assertIn("document.permission_sync_state", boundary)
        self.assertIn("Each person must first sign in to Paperless", boundary)


if __name__ == "__main__":
    unittest.main()
