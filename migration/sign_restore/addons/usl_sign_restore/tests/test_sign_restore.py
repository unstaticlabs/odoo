from odoo.tests.common import TransactionCase

from ..models.restore import FIELD_XMLIDS, UslSignRestoreRun, request_fingerprint


class TestSignRestore(TransactionCase):
    def test_request_fingerprint_is_stable_and_signer_order_independent(self):
        first = request_fingerprint("original", "final", "Agreement", "2025-01-02 03:04:05", ["B@example.com", "a@example.com"])
        second = request_fingerprint("original", "final", "Agreement", "2025-01-02 03:04:05", ["A@example.com", "b@example.com"])
        self.assertEqual(first, second)

    def test_source_field_types_have_native_targets(self):
        for item_type in ("signature", "initials", "name", "email", "phone", "company", "text", "textarea", "date"):
            self.assertTrue(self.env.ref(FIELD_XMLIDS[item_type]))

    def test_completion_pair_requires_exact_signed_and_certificate_files(self):
        signed, certificate = UslSignRestoreRun._completion_pair([{"name": "signed.pdf"}, {"name": "completion certificate.pdf"}])
        self.assertEqual(signed["name"], "signed.pdf")
        self.assertEqual(certificate["name"], "completion certificate.pdf")
        self.assertEqual(UslSignRestoreRun._completion_pair([{"name": "signed.pdf"}]), (None, None))

