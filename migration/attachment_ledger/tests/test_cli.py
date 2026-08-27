import tempfile
import unittest
from pathlib import Path

from migration.attachment_ledger.cli import classify_attachment
from migration.source_truth.cli import verify_filestore


def row(**values):
    return {
        "id": 1,
        "store_fname": "ab/checksum",
        "checksum": "",
        "file_size": 4,
        "type": "binary",
        "mimetype": "application/pdf",
        "res_model": "",
        "res_id": 0,
        "res_field": "",
        "has_db_data": False,
        "url": "",
        **values,
    }


class AttachmentClassificationTest(unittest.TestCase):
    def classify(self, values, *, documents=None, messages=None, sign=None):
        return classify_attachment(
            row(**values),
            document_rows=documents or [],
            message_attachment_ids=messages or set(),
            sign_document_attachment_ids=sign or set(),
        )

    def test_business_document_has_operational_and_archive_actions(self):
        actions = self.classify(
            {"res_model": "account.move", "res_id": 41},
            documents=[{"id": 91}],
        )
        self.assertEqual(
            {item["kind"] for item in actions},
            {"restore_operational_attachment", "archive_document_original"},
        )
        self.assertEqual(
            {item["state"] for item in actions},
            {"implemented"},
        )

    def test_only_high_resolution_image_is_restored(self):
        primary = self.classify(
            {"res_model": "res.partner", "res_field": "image_1920"},
        )
        derivative = self.classify(
            {"res_model": "res.partner", "res_field": "image_128"},
        )
        self.assertEqual(primary[0]["kind"], "restore_native_binary_field")
        self.assertEqual(primary[0]["state"], "implemented")
        self.assertEqual(derivative[0]["kind"], "regenerate_derivative")

    def test_orphan_binary_is_never_silently_dropped(self):
        actions = self.classify({})
        self.assertEqual(actions[0]["kind"], "archive_unassigned_evidence")
        self.assertEqual(actions[0]["state"], "implemented")

    def test_generated_company_stylesheet_is_recomputed(self):
        actions = self.classify({"name": "res.company.scss", "mimetype": "text/scss"})
        self.assertEqual(actions[0]["kind"], "recompute_distribution_asset")
        self.assertEqual(actions[0]["state"], "implemented")

    def test_private_key_is_explicitly_not_copied(self):
        actions = self.classify(
            {"res_model": "certificate.key", "res_field": "pem_key"},
        )
        self.assertEqual(actions[0]["kind"], "revoke_and_reenroll")
        self.assertEqual(actions[0]["state"], "implemented")

    def test_demo_knowledge_attachment_is_explicitly_discarded(self):
        actions = self.classify(
            {"res_model": "knowledge.article", "res_field": "cover_image"},
            messages={1},
        )
        self.assertEqual(actions[0]["kind"], "discard_demo_knowledge_attachment")
        self.assertEqual(actions[0]["scope"], "knowledge")
        self.assertEqual(actions[0]["state"], "implemented")

    def test_native_dashboard_payload_is_explicitly_recomputed(self):
        actions = self.classify(
            {
                "res_model": "spreadsheet.dashboard",
                "res_id": 1,
                "res_field": "spreadsheet_binary_data",
            },
        )
        disposition = next(
            item for item in actions if item["kind"] == "recompute_distribution_asset"
        )
        self.assertEqual(disposition["scope"], "preferences")
        self.assertEqual(disposition["state"], "implemented")

    def test_enterprise_sample_dashboard_payload_is_explicitly_dropped(self):
        actions = self.classify(
            {
                "res_model": "spreadsheet.dashboard",
                "res_id": 12,
                "res_field": "spreadsheet_binary_data",
            },
        )
        disposition = next(
            item for item in actions if item["kind"] == "deliberately_not_copied"
        )
        self.assertEqual(disposition["scope"], "preferences")
        self.assertEqual(disposition["state"], "implemented")

    def test_unknown_dashboard_payload_remains_blocking(self):
        actions = self.classify(
            {
                "res_model": "spreadsheet.dashboard",
                "res_id": 999,
                "res_field": "spreadsheet_binary_data",
            },
        )
        self.assertEqual(actions[0]["kind"], "resolve_downstream_owner")
        self.assertEqual(actions[0]["state"], "pending")

    def test_ai_source_pdf_becomes_restricted_business_evidence(self):
        actions = self.classify({"res_model": "ai.agent.source"})
        disposition = next(
            item
            for item in actions
            if item["kind"] == "archive_restricted_business_evidence"
        )
        self.assertEqual(disposition["scope"], "documents")
        self.assertEqual(disposition["state"], "implemented")

    def test_knowledge_url_is_explicitly_dropped(self):
        actions = self.classify(
            {"type": "url", "res_model": "knowledge.cover", "store_fname": ""},
        )
        self.assertEqual(actions[0]["kind"], "discard_demo_knowledge_attachment")
        self.assertEqual(actions[0]["scope"], "knowledge")

    def test_sign_and_ownerless_chatter_evidence_are_implemented(self):
        sign = self.classify({"id": 8, "res_model": "sign.request"}, sign={8})
        chatter = self.classify({"id": 9}, messages={9})
        signing_action = next(
            item for item in sign if item["kind"] == "archive_signing_evidence"
        )
        self.assertEqual(signing_action["state"], "implemented")
        self.assertEqual(
            next(item for item in chatter if item["kind"] == "restore_collaboration_attachment")["state"],
            "implemented",
        )

    def test_sign_business_evidence_is_archived(self):
        actions = self.classify({"res_model": "sign.request", "res_id": 2})
        self.assertEqual(actions[0]["kind"], "archive_signing_evidence")
        self.assertEqual(actions[0]["state"], "implemented")

    def test_rendered_and_reusable_signing_marks_cannot_be_reused(self):
        rendered = self.classify(
            {"res_model": "sign.request.item", "res_field": "signature"},
        )
        preference = self.classify(
            {"res_model": "res.users", "res_field": "sign_signature_data"},
        )
        self.assertEqual(rendered[0]["kind"], "retain_rendered_mark_in_signed_result")
        self.assertEqual(preference[0]["kind"], "discard_reusable_signing_preference")
        self.assertEqual({rendered[0]["state"], preference[0]["state"]}, {"implemented"})

    def test_file_integrity_is_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            filestore = Path(directory)
            source = filestore / "ab/checksum"
            source.parent.mkdir()
            source.write_bytes(b"data")
            summary, errors = verify_filestore(filestore, [row()])
            self.assertEqual(errors, [])
            self.assertEqual(summary["checked_stored_objects"], 1)
            source.write_bytes(b"changed")
            _, errors = verify_filestore(filestore, [row()])
            self.assertEqual(errors[0]["error"], "file size differs")


if __name__ == "__main__":
    unittest.main()
