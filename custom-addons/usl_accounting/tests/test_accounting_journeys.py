from odoo import Command
from odoo.tests import new_test_user, tagged

from odoo.addons.usl_docs.tests.journey import JourneyCase

from .test_immediate_settlement_tour import TestImmediateSettlementBrowser

BILLS = "/odoo/action-account.action_move_in_invoice_type"
IMPORTS = "/odoo/action-usl_accounting.action_bank_ingestion_config"


@tagged("post_install", "-at_install", "usl_docs_journey")
class TestImmediateSettlementJourney(TestImmediateSettlementBrowser, JourneyCase):
    """The how-to for settling a foreign bill at the payment rate.

    The fixtures are the browser tour's own: a synthetic USD bill and the
    EUR card debit that pays it.
    """

    def test_settle_a_foreign_bill_at_the_payment_rate(self):
        accountant = new_test_user(
            self.env,
            name="Journey Accountant",
            login="immediate_settlement_journey_accountant",
            groups="account.group_account_manager,base.group_multi_currency",
            company_id=self.company.id,
            company_ids=[Command.set(self.company.ids)],
        )
        bill = self.bill.with_user(accountant)
        _liquidity, source_line, _other = self.statement_line._seek_for_lines()
        eligibility = bill._get_immediate_settlement_eligibility(source_line)
        self.assertTrue(eligibility["eligible"], eligibility["reason"])
        widget_line = next(
            line for line in bill.invoice_outstanding_credits_debits_widget["content"] if line["id"] == source_line.id
        )
        self.assertTrue(widget_line["can_immediate_settle"], widget_line["immediate_settlement_reason"])
        self.assertTrue(widget_line["can_use_payment_rate"], widget_line["payment_rate_settlement_reason"])
        self.assertEqual(widget_line["recommended_settlement_action"], "payment_rate")
        self.run_journey(
            "usl_immediate_settlement",
            f"/odoo/account.move/{self.bill.id}",
            login=accountant.login,
            screen=BILLS,
        )
        settlement = self.bill.immediate_settlement_ids
        self.assertEqual(settlement.state, "reversed")
        self.assertEqual(settlement.mechanism, "payment_rate")
        self.assertEqual(self.bill.payment_state, "not_paid")


@tagged("post_install", "-at_install", "usl_docs_journey")
class TestBankPeriodJourney(JourneyCase):
    """The how-to for correcting an imported statement's period."""

    def test_correct_a_bank_statement_period(self):
        accountant = new_test_user(
            self.env,
            name="Journey Accountant",
            login="bank_period_journey_accountant",
            groups="account.group_account_user",
            company_id=self.env.company.id,
            company_ids=[Command.set(self.env.company.ids)],
        )
        journal = self.env["account.journal"].create({"name": "Period journey", "code": "PJR", "type": "bank"})
        config = self.env["account.bank.ingestion.config"].create({
            "name": "Period journey",
            "journal_id": journal.id,
            "source_account_identifier": "FR7630001007941234567890185",
            "automatic_start_date": "2026-07-01",
        })
        ingestion = self.env["account.bank.ingestion"].create({
            "name": "Period journey",
            "subject": "Scheduled bank export",
            "config_id": config.id,
            "state": "attention",
            "period_start": "2026-07-01",
            "period_end": "2026-07-31",
        })
        self.run_journey(
            "usl_bank_period_correction",
            f"/odoo/account.bank.ingestion/{ingestion.id}",
            login=accountant.login,
            screen=IMPORTS,
        )
        ingestion.invalidate_recordset()
        self.assertEqual(str(ingestion.period_start), "2026-08-01")
        self.assertEqual(str(ingestion.period_end), "2026-08-31")
