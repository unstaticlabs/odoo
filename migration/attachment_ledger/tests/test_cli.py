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

    def test_sign_and_ownerless_chatter_evidence_are_implemented(self):
        sign = self.classify({"id": 8, "res_model": "sign.request"}, sign={8})
        chatter = self.classify({"id": 9}, messages={9})
        self.assertEqual(
            next(item for item in sign if item["kind"] == "archive_signing_evidence")["state"],
            "implemented",
        )
        self.assertEqual(
            next(item for item in chatter if item["kind"] == "restore_collaboration_attachment")["state"],
            "implemented",
        )

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
