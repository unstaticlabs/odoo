"""Mounted native Chromium coverage for source evidence and stale navigation."""

from odoo.addons.web.tests.test_js import HootCommon, unit_test_error_checker
from odoo.tests import tagged


@tagged("post_install", "-at_install", "usl_task_chatter_search_ui")
class TestTaskChatterSearchUi(HootCommon):
    def test_source_evidence_transport(self):
        suite = self._generate_hash("@usl_home/content_search")
        self.browser_js(
            f"/web/tests?headless&loglevel=2&preset=desktop&timeout=15000&id={suite}",
            "", "", login="admin", timeout=180,
            success_signal="[HOOT] Test suite succeeded",
            error_checker=unit_test_error_checker,
        )
