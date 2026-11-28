"""Native Chromium assertions for the readonly receipt email link."""
from odoo.addons.web.tests.test_js import HootCommon, unit_test_error_checker
from odoo.tests import tagged


@tagged("post_install", "-at_install", "usl_expense_email_receipt_ui")
class TestExpenseEmailReceiptUi(HootCommon):
    def test_receipt_email_link(self):
        suite = self._generate_hash("@usl_accounting/expense_receipt_email")
        self.browser_js(
            f"/web/tests?headless&loglevel=2&preset=desktop&timeout=15000&id={suite}",
            "", "", login="admin", timeout=180,
            success_signal="[HOOT] Test suite succeeded", error_checker=unit_test_error_checker,
        )
