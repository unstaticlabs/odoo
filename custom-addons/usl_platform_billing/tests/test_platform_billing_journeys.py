from odoo import fields
from odoo.tests import tagged

from odoo.addons.usl_docs.tests.journey import JourneyCase

from .test_platform_billing_browser import TestPlatformBillingBrowser

SESSIONS = "/odoo/action-usl_platform_billing.action_platform_billing_sessions"


@tagged("post_install", "-at_install", "usl_docs_journey")
class TestPlatformBillingJourneys(TestPlatformBillingBrowser, JourneyCase):
    """The how-to guides for Platform billing, generated from these journeys.

    The fixtures are the browser tour's own: a synthetic platform, a
    synthetic session and a synthetic bank receipt.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # The fixtures' generated names would show in the screenshots.
        cls.operator.name = "Journey Operator"
        cls.manager.name = "Journey Manager"

    def test_create_a_monthly_platform_billing_session(self):
        closed_session = self.env["usl.platform.billing.session"].create({
            "name": "Browser closed session",
            "company_id": self.company.id,
            "period_month": fields.Date.from_string("2026-06-01"),
            "invoice_date": fields.Date.from_string("2026-06-30"),
            "bank_currency_id": self.currency.id,
        })
        closed_session._workflow_write({"state": "cancelled"})
        self.platform.copy({"name": "Browser platform without August payout"})
        action = self.env.ref("usl_platform_billing.action_platform_billing_sessions")
        # Keep the calendar navigation deterministic across execution dates.
        action.context = "{'default_period_month': '2026-07-01', 'default_invoice_date': '2026-07-31'}"
        self.run_journey(
            "usl_platform_billing_operator_creation_journey",
            SESSIONS,
            login=self.operator.login,
        )
        session = self.env["usl.platform.billing.session"].search([
            ("company_id", "=", self.company.id),
            ("name", "=", "Août 2026"),
        ])
        self.assertEqual(session.state, "posted")
        self.assertEqual(session.customer_invoice_ids.state, "posted")

    def test_settle_a_platform_billing_session(self):
        self.run_journey(
            "usl_platform_billing_manager_journey",
            f"{SESSIONS}/{self.billing_session.id}",
            login=self.manager.login,
            screen=SESSIONS,
        )
        self.assertEqual(self.billing_session.state, "paid")
        self.assertTrue(self.bank_line.is_reconciled)
