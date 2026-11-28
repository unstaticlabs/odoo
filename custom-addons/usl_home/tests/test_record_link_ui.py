"""Execute the record-link suite in native desktop and mobile Chromium."""

from odoo.addons.web.tests.test_js import HootCommon, unit_test_error_checker
from odoo.tests import tagged


class RecordLinkSuite:
    def _run_record_link_suite(self, preset):
        suite = self._generate_hash("@usl_home/record_link")
        self.browser_js(
            f"/web/tests?headless&loglevel=2&preset={preset}&timeout=15000&id={suite}",
            "", "", login="admin", timeout=180,
            success_signal="[HOOT] Test suite succeeded",
            error_checker=unit_test_error_checker,
        )


@tagged("post_install", "-at_install", "usl_record_link_ui")
class TestRecordLinkDesktop(RecordLinkSuite, HootCommon):
    def test_copy_record_link(self):
        self._run_record_link_suite("desktop")


@tagged("post_install", "-at_install", "usl_record_link_ui")
class TestRecordLinkMobile(RecordLinkSuite, HootCommon):
    browser_size = "390x844"
    touch_enabled = True

    def test_copy_record_link(self):
        self._run_record_link_suite("mobile")
