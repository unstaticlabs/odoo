"""Native Chromium regression for reviewing persisted incomplete purchase mail."""

from odoo.addons.web.tests.test_js import HootCommon, unit_test_error_checker
from odoo.tests import tagged


@tagged("post_install", "-at_install", "usl_purchase_mail_grouping_ui")
class TestPurchaseMailGroupingUi(HootCommon):
    def test_persisted_bill_review_transport(self):
        suite = self._generate_hash("@usl_accounting/purchase_mail_grouping_form")
        self.browser_js(
            f"/web/tests?headless&loglevel=2&preset=desktop&timeout=15000&id={suite}",
            "", "", login="admin", timeout=180,
            success_signal="[HOOT] Test suite succeeded",
            error_checker=unit_test_error_checker,
        )
