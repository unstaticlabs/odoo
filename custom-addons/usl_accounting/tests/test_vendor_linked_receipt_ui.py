"""Native Chromium regression for recovering the source before completing a bill."""
from urllib.parse import urlencode

from odoo.addons.web.tests.test_js import HootCommon, unit_test_error_checker
from odoo.tests import tagged


@tagged("post_install", "-at_install", "usl_vendor_linked_receipt_ui")
class TestVendorLinkedReceiptUi(HootCommon):
    def test_source_first_bill_controls(self):
        suite = self._generate_hash("@usl_accounting/linked_bill_receipt_form")
        picker_arch = self.env["usl.mail.pdf.candidate.wizard"].get_view(
            self.env.ref("usl_accounting.view_usl_mail_pdf_candidate_wizard_form").id, "form",
        )["arch"]
        query = urlencode({"qa_picker_arch": picker_arch})
        self.browser_js(
            f"/web/tests?headless&loglevel=2&preset=desktop&timeout=15000&id={suite}&{query}",
            "", "", login="admin", timeout=180,
            success_signal="[HOOT] Test suite succeeded",
            error_checker=unit_test_error_checker,
        )
