from lxml import etree
from unittest.mock import patch

from odoo.tests import tagged
from odoo.tools import BinaryBytes, file_open

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.addons.account_edi_ubl_cii.models.account_edi_common import AccountEdiCommon


@tagged("post_install", "-at_install", "usl_edi_line_compatibility")
class TestEdiLineCompatibility(AccountTestInvoicingCommon):
    def test_native_ubl_import_without_enterprise_deferred_fields(self):
        with file_open("usl_accounting/tests/fixtures/mail_invoice.xml", "rb") as source:
            raw = source.read()
        bill = self.env["account.move"].create({
            "move_type": "in_invoice",
            "journal_id": self.company_data["default_journal_purchase"].id,
        })
        attachment = self.env["ir.attachment"].create({
            "name": "invented-invoice.xml", "raw": BinaryBytes(raw),
            "res_model": bill._name, "res_id": bill.id,
        })
        self.assertTrue(bill._extend_with_attachments(bill._to_files_data(attachment), new=True))
        self.assertEqual(bill.ref, "QA-UBL-001")
        self.assertEqual(len(bill.invoice_line_ids), 1)
        self.assertEqual(bill.amount_total, 120)
        self.assertEqual(bill.state, "draft")
        self.assertEqual(attachment.raw.content, raw)

    def test_native_line_values_omit_only_absent_empty_optional_fields(self):
        with file_open("usl_accounting/tests/fixtures/mail_invoice.xml", "rb") as source:
            tree = etree.fromstring(source.read())
        line = tree.find("{*}InvoiceLine")
        bill = self.env["account.move"].new({"move_type": "in_invoice"})
        values = self.env["account.edi.xml.ubl_21"]._retrieve_invoice_line_vals(bill, line)
        for name in ("deferred_start_date", "deferred_end_date"):
            if name not in self.env["account.move.line"]._fields:
                self.assertNotIn(name, values)
        self.assertEqual(values["price_subtotal"], 120)

    def test_present_optional_fields_retain_empty_and_real_values(self):
        # Simulate the optional addon's registry shape at this delegate seam;
        # the real decoder/import is exercised separately above.
        names = ("deferred_start_date", "deferred_end_date")
        line_fields = self.env["account.move.line"]._fields
        for value in (None, "2026-10-01"):
            expected = dict.fromkeys(names, value)
            with patch.object(type(self.env["account.move.line"]), "_fields", {
                **line_fields, **dict.fromkeys(names, object()),
            }):
                with patch.object(AccountEdiCommon, "_retrieve_invoice_line_vals", return_value=expected.copy()):
                    actual = self.env["account.edi.common"]._retrieve_invoice_line_vals(None, None)
            self.assertEqual(actual, expected)

    def test_absent_optional_nonempty_value_keeps_native_error(self):
        name = "deferred_start_date"
        if name in self.env["account.move.line"]._fields:
            self.skipTest("Optional deferred field is installed")
        expected = {name: "2026-10-01", "deferred_end_date": None}
        with patch.object(AccountEdiCommon, "_retrieve_invoice_line_vals", return_value=expected):
            actual = self.env["account.edi.common"]._retrieve_invoice_line_vals(None, None)
        self.assertEqual(actual, {name: "2026-10-01"})
        bill = self.env["account.move"].create({
            "move_type": "in_invoice",
            "journal_id": self.company_data["default_journal_purchase"].id,
        })
        with self.assertRaisesRegex(ValueError, "Invalid field 'deferred_start_date'"):
            self.env["account.move.line"].create({"move_id": bill.id, **actual})
