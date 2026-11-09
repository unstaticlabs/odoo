from odoo.tests import tagged

from odoo.addons.usl_docs.tests.journey import JourneyCase

from .test_einvoice_reception import EinvoiceTestDataMixin

READINESS = "/odoo/action-rebuild_account_migration.action_rebuild_einvoice_readiness"


@tagged("post_install", "-at_install", "usl_docs_journey")
class TestEinvoiceJourneys(EinvoiceTestDataMixin, JourneyCase):
    """The how-to for the reception self-check, generated from the browser journey.

    The fixture is the reception tests' own synthetic company, with an
    offline provider; nothing leaves the test database.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls._configure_company()

    def test_run_the_electronic_invoice_reception_self_check(self):
        self.run_journey("usl_einvoice_manager_reception", READINESS, login=self.manager.login)
        self.assertEqual(self.company.rebuild_einvoice_test_status, "passed")
        self.assertTrue(self.company.rebuild_einvoice_test_current)
        self.assertFalse(self.company.rebuild_einvoice_test_reception_id)
