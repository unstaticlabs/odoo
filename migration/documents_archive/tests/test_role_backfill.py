import unittest

from migration.documents_archive.role_backfill import (
    resolve_link_role,
    resolve_root_role,
)


class DocumentsRoleBackfillTest(unittest.TestCase):
    def test_accounting_and_hr_relationships_are_evidence(self):
        for model in ("account.move", "account.payment", "hr.expense", "hr.employee"):
            with self.subTest(model=model):
                policy = resolve_root_role(record_models=[model])
                self.assertEqual(policy["archive_mode"], "mandatory")
                self.assertEqual(policy["document_role"], "evidence")

    def test_historical_project_task_and_contact_context_is_background(self):
        policy = resolve_root_role(
            record_models=["project.project", "project.task", "res.partner"],
        )
        self.assertEqual(policy["document_role"], "background")
        self.assertEqual(
            policy["policy_reason"],
            "migration_historical_record_context",
        )

    def test_curated_legal_classification_is_library(self):
        policy = resolve_root_role(
            record_models=["res.partner"],
            tags=["Contracts & legal"],
        )
        self.assertEqual(policy["document_role"], "library")
        self.assertEqual(policy["policy_reason"], "migration_curated_library")

    def test_unlinked_explicit_upload_is_library_but_external_is_background(self):
        explicit = resolve_root_role(record_models=[])
        external = resolve_root_role(
            record_models=[],
            explicit_documents_record=False,
        )
        self.assertEqual(explicit["document_role"], "library")
        self.assertEqual(external["document_role"], "background")

    def test_evidence_root_makes_every_business_link_evidence(self):
        root = resolve_root_role(record_models=["account.move", "res.partner"])
        partner = resolve_link_role(res_model="res.partner", root_policy=root)
        self.assertEqual(partner["document_role"], "evidence")

    def test_existing_evidence_is_never_demoted_by_missing_source_context(self):
        policy = resolve_root_role(
            record_models=["project.project"],
            existing_role="evidence",
        )
        self.assertEqual(policy["archive_mode"], "mandatory")
        self.assertEqual(policy["document_role"], "evidence")
        self.assertEqual(policy["policy_reason"], "migration_preserved_evidence")

    def test_background_relationship_does_not_demote_curated_root(self):
        root = resolve_root_role(
            record_models=["res.partner"],
            tags=["Company records"],
        )
        partner = resolve_link_role(res_model="res.partner", root_policy=root)
        self.assertEqual(root["document_role"], "library")
        self.assertEqual(partner["document_role"], "background")


if __name__ == "__main__":
    unittest.main()
