"""Native Chromium regression for recovering the source before completing a bill."""
from odoo.addons.web.tests.test_js import HootCommon, unit_test_error_checker
from odoo.tests import tagged


@tagged("post_install", "-at_install", "usl_vendor_linked_receipt_ui")
class TestVendorLinkedReceiptUi(HootCommon):
    def test_source_first_bill_controls(self):
        suite = self._generate_hash("@usl_accounting/linked_bill_receipt_form")
        self.browser_js(
            f"/web/tests?headless&loglevel=2&preset=desktop&timeout=15000&id={suite}",
            "", "", login="admin", timeout=180,
            success_signal="[HOOT] Test suite succeeded",
            error_checker=unit_test_error_checker,
        )
