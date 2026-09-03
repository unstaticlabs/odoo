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
                "account_group_compat.py": [
                    "account.group",
                ],
                "bank_statement_ingestion.py": [
                    "account.bank.ingestion.config",
                    "account.bank.ingestion",
                    "account.bank.ingestion.file",
                ],
                "bank_statement_review.py": [
                    "account.bank.statement",
                    "account.bank.statement.certification",
                    "account.bank.statement.exception",
                ],
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
                r"MM/DD/YYYY|MM/dd/yyyy|%m/%d/%Y",
            ),
        }
        violations = []
        for suffix in ("*.js", "*.xml"):
            for path in CUSTOM_ADDONS.glob(f"*/static/src/**/{suffix}"):
                source = path.read_text(encoding="utf-8")
                for label, pattern in forbidden_patterns.items():
                    if pattern.search(source):
                        violations.append(
                            f"{path.relative_to(REPOSITORY_ROOT)}: {label}",
                        )
        self.assertEqual(
            violations,
            [],
            "product UI must use Odoo date components and DD/MM/YYYY: "
            + ", ".join(violations),
        )

    def test_obsolete_placeholder_is_absent(self):
        self.assertNotIn("usl_custom_placeholder", _manifests())

    def test_project_restore_declares_temporary_accounting_dependency(self):
        manifest = ast.literal_eval(
            (
                REPOSITORY_ROOT
                / "migration/project_restore/addons/usl_project_restore/__manifest__.py"
            ).read_text(encoding="utf-8"),
        )
        self.assertIn("usl_accounting_restore", manifest["depends"])
        self.assertNotIn("rebuild_account_migration", manifest["depends"])

        project_script = (REPOSITORY_ROOT / "migration/internal/project-restore").read_text(
            encoding="utf-8",
        )
        self.assertIn("scripts/lib/migration-addons.sh", project_script)

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

        tese_script = (REPOSITORY_ROOT / "migration/internal/tese-restore").read_text(
            encoding="utf-8",
        )
        self.assertIn("scripts/lib/migration-addons.sh", tese_script)
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

    def test_platform_finalization_defers_product_registry_until_global_cleanup(self):
        platform_script = (
            REPOSITORY_ROOT / "migration/internal/platform-billing-restore"
        ).read_text(encoding="utf-8")
        target_script = (REPOSITORY_ROOT / "migration/internal/reconstruct").read_text(
            encoding="utf-8",
        )

        self.assertIn(
            "PLATFORM_BILLING_RESTORE_DEFER_PRODUCT_FINALIZE",
            platform_script,
        )
        self.assertIn("schema-finalize)", platform_script)
        self.assertIn(
            "PLATFORM_BILLING_RESTORE_DEFER_PRODUCT_FINALIZE=1",
            target_script,
        )

    def test_staged_migration_runners_share_the_complete_temporary_registry(self):
        helper_path = REPOSITORY_ROOT / "scripts/lib/migration-addons.sh"
        helper = helper_path.read_text(encoding="utf-8")
        match = re.search(
            r'^USL_MIGRATION_ADDONS_PATH="([^"]+)"$',
            helper,
            re.MULTILINE,
        )
        self.assertIsNotNone(match)
        addons_path = match.group(1)
        temporary_mounts = {
            "/mnt/accounting-migration-addons",
            "/mnt/identity-migration-addons",
            "/mnt/product-migration-addons",
            "/mnt/b2c-migration-addons",
            "/mnt/hr-migration-addons",
            "/mnt/project-migration-addons",
            "/mnt/tese-migration-addons",
            "/mnt/platform-billing-migration-addons",
            "/mnt/collaboration-migration-addons",
        }
        self.assertEqual(
            {path for path in addons_path.split(",") if path in temporary_mounts},
            temporary_mounts,
        )
        collaboration_script = (
            REPOSITORY_ROOT / "migration/internal/collaboration-restore"
        ).read_text(encoding="utf-8")
        self.assertIn(
            'migration_addons_path="$USL_MIGRATION_ADDONS_PATH"',
            collaboration_script,
        )

        for script_name in (
            "accounting-restore",
            "b2c-restore",
            "documents-restore",
            "hr-restore",
            "identity-restore",
            "platform-billing-restore",
            "product-restore",
            "project-restore",
            "tese-restore",
        ):
            script = (REPOSITORY_ROOT / "migration/internal" / script_name).read_text(
                encoding="utf-8",
            )
            self.assertIn("scripts/lib/migration-addons.sh", script, script_name)
            self.assertIn(
                'migration_addons_path="$USL_MIGRATION_ADDONS_PATH"',
                script,
                script_name,
            )

        compose = (REPOSITORY_ROOT / "compose.yaml").read_text(encoding="utf-8")

        def service(name):
            match = re.search(
                rf"^  {re.escape(name)}:\n(.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)",
                compose,
                re.MULTILINE | re.DOTALL,
            )
            self.assertIsNotNone(match, name)
            return match.group(1)

        for service_name in (
            "accounting-migration",
            "project-migration",
            "b2c-migration",
            "tese-migration",
            "platform-billing-migration",
        ):
            section = service(service_name)
            self.assertIn(f"--addons-path={addons_path}", section, service_name)
            for mount in temporary_mounts:
                self.assertIn(f"{mount}:ro", section, service_name)

        test_service = service("test")
        for mount in temporary_mounts:
            self.assertIn(f"{mount}:ro", test_service)

        project_service = service("project-migration")
        self.assertIn("/mnt/collaboration-migration-addons:ro", project_service)

        product_service = service("odoo")
        for mount in temporary_mounts:
            self.assertNotIn(mount, product_service)


if __name__ == "__main__":
    unittest.main()
