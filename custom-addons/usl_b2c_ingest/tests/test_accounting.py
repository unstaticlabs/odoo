"""Invoicing a drop, settling it, and refusing to post the wrong numbers."""

from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import tagged

from . import fixtures
from .test_materialise import TestMaterialise


@tagged("post_install", "-at_install", "usl_b2c_ingest")
class TestAccounting(TestMaterialise):
    """Revenue, VAT and commission land where each of them belongs."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.clearing = cls._account("Invented clearing", "asset_current")
        cls.sale_journal = cls.env["account.journal"].search(
            [("company_id", "=", cls.company.id), ("type", "=", "sale")], limit=1,
        )
        cls.pending_vat = cls._account("Invented VAT to regularise", "liability_current")
        cls.collected_vat = cls._account("Invented VAT collected", "liability_current")
        cls.commissions = cls._account("Invented commissions", "expense")
        cls.journal = cls.env["account.journal"].create(
            {
                "name": "Invented clearing",
                "code": "INVC",
                "type": "bank",
                "company_id": cls.company.id,
                "default_account_id": cls.clearing.id,
            },
        )
        (
            cls.journal.inbound_payment_method_line_ids
            | cls.journal.outbound_payment_method_line_ids
        ).payment_account_id = cls.clearing
        cls.commission_product = cls.env["product.product"].create(
            {
                "name": "Invented commission",
                "type": "service",
                "purchase_ok": True,
                "supplier_taxes_id": [(5, 0, 0)],
                "property_account_expense_id": cls.commissions.id,
            },
        )
        cls.operator = cls.env["res.partner"].create(
            {"name": "Invented Channel Operator", "country_id": cls.env.ref("base.ie").id},
        )
        for channel in cls.channels.values():
            channel.write(
                {
                    "clearing_journal_ids": [(6, 0, cls.journal.ids)],
                    "operator_partner_id": cls.operator.id,
                    "fee_product_id": cls.commission_product.id,
                },
            )
        cls.company.write(
            {
                "usl_b2c_pending_vat_account_id": cls.pending_vat.id,
                "usl_b2c_oss_vat_account_id": cls.collected_vat.id,
            },
        )

    def _applied(self):
        batch, _jersey, _cap = self._etsy_drop()
        batch.action_apply()
        return batch

    def _balances(self, moves):
        lines = moves.line_ids | moves.mapped("matched_payment_ids.move_id.line_ids")
        found = {}
        for line in lines:
            found[line.account_id] = round(found.get(line.account_id, 0) + line.balance, 2)
        return found

    # -- invoicing ---------------------------------------------------------

    def test_every_sale_is_invoiced_and_settled_to_the_channel(self):
        batch = self._applied()
        batch.action_invoice()
        self.assertEqual(len(batch.invoiced_move_ids), 2)
        self.assertEqual(set(batch.invoiced_move_ids.mapped("state")), {"posted"})
        self.assertEqual(set(batch.invoiced_move_ids.mapped("payment_state")), {"paid"})
        balances = self._balances(batch.invoiced_move_ids)
        # 45.00 and 69.00 collected by the channel, none of it owed by a customer.
        self.assertAlmostEqual(balances[self.clearing], 114.00, places=2)
        self.assertAlmostEqual(balances.get(self.income, 0) + balances.get(
            self.carriage_account, 0) + balances.get(self.pending_vat, 0), -114.00, places=2)

    def test_an_invoice_is_dated_the_day_the_sale_happened(self):
        batch = self._applied()
        batch.action_invoice()
        invoice = self._sale(batch, "9000000001").invoice_ids
        self.assertEqual(str(invoice.invoice_date), "2026-03-04")
        self.assertEqual(str(invoice.date), "2026-03-04")

    def test_carriage_and_goods_do_not_share_an_account(self):
        batch = self._applied()
        batch.action_invoice()
        balances = self._balances(batch.invoiced_move_ids)
        self.assertLess(balances[self.carriage_account], 0)
        self.assertLess(balances[self.income], 0)

    def test_destination_vat_accrues_where_the_shop_can_pay_it(self):
        batch = self._applied()
        batch.action_park_destination_vat()
        batch.action_invoice()
        balances = self._balances(batch.invoiced_move_ids)
        self.assertLess(balances[self.pending_vat], 0)
        self.assertNotIn(self.collected_vat, balances)

    def test_registering_moves_destination_vat_to_the_collected_account(self):
        self.company.usl_b2c_oss_registered = True
        batch = self._applied()
        batch.action_park_destination_vat()
        batch.action_invoice()
        balances = self._balances(batch.invoiced_move_ids)
        self.assertLess(balances[self.collected_vat], 0)
        self.assertNotIn(self.pending_vat, balances)

    def test_invoicing_twice_posts_one_invoice_each(self):
        batch = self._applied()
        batch.action_invoice()
        first = batch.invoiced_move_ids
        batch.action_invoice()
        self.assertEqual(batch.invoiced_move_ids, first)

    def test_a_closed_period_is_not_written_into(self):
        batch = self._applied()
        self.company.fiscalyear_lock_date = "2026-12-31"
        with self.assertRaises(UserError) as caught:
            batch.action_invoice()
        self.assertIn("closed", str(caught.exception))

    def test_a_channel_with_nowhere_to_hold_receipts_is_refused(self):
        batch = self._applied()
        self.channels["etsy"].clearing_journal_ids = [(5, 0, 0)]
        with self.assertRaises(UserError) as caught:
            batch.action_invoice()
        self.assertIn("clearing journal", str(caught.exception))

    # -- settlement --------------------------------------------------------

    def test_the_commission_leaves_the_clearing_account(self):
        batch = self._applied()
        batch.action_invoice()
        batch.action_bill_fees()
        # 2.00 kept on the March order and 3.00 on the other, both in March.
        self.assertEqual(len(batch.fee_bill_ids), 1)
        self.assertAlmostEqual(batch.fee_bill_ids.amount_total, 5.00, places=2)
        balances = self._balances(batch.invoiced_move_ids | batch.fee_bill_ids)
        self.assertAlmostEqual(balances[self.clearing], 109.00, places=2)
        self.assertAlmostEqual(balances[self.commissions], 5.00, places=2)

    def test_a_commission_is_billed_once_per_channel_and_month(self):
        batch = self._applied()
        batch.action_invoice()
        batch.action_bill_fees()
        first = batch.fee_bill_ids
        batch.action_bill_fees()
        self.assertEqual(batch.fee_bill_ids, first)

    def test_a_commission_on_a_sale_odoo_already_held_is_not_billed_again(self):
        batch = self._applied()
        batch.action_invoice()
        batch.action_bill_fees()
        billed = batch.fee_bill_ids.amount_total
        again = self._batch(
            **{
                "etsy-orders.csv": fixtures.etsy_orders(),
                "etsy-items.csv": fixtures.etsy_order_items(),
            },
        )
        again.action_parse()
        again.action_resolve()
        again.action_apply()
        again.action_bill_fees()
        self.assertFalse(again.fee_bill_ids)
        self.assertAlmostEqual(batch.fee_bill_ids.amount_total, billed, places=2)

    def test_fees_cannot_be_billed_before_the_drop_is_applied(self):
        batch, _jersey, _cap = self._etsy_drop()
        with self.assertRaises(UserError):
            batch.action_bill_fees()

    # -- readiness ---------------------------------------------------------

    def test_a_shadowing_position_is_reported_and_can_be_retired(self):
        shadow = self._position("Invented catch-all", False, self.home_tax, sequence=1)
        batch, _jersey, _cap = self._etsy_drop()
        # What the check reports, with the stage that would put it right first
        # turned off. That the stage does put it right is its own test.
        batch.correct_chart = False
        batch.action_check_readiness()
        findings = batch.issue_ids.filtered(
            lambda issue: issue.kind == "shadowed_destination",
        )
        self.assertTrue(findings)
        batch.action_retire_shadowing_positions()
        self.assertFalse(shadow.active)
        self.assertFalse(
            batch.issue_ids.filtered(lambda issue: issue.kind == "shadowed_destination"),
        )

    def test_a_product_with_two_taxes_is_reported_and_can_be_settled(self):
        batch, jersey, _cap = self._etsy_drop()
        second = self._tax("Invented 20% FR other", 20, self.france)
        jersey.product_tmpl_id.taxes_id = [(4, second.id)]
        batch.correct_chart = False
        batch.action_check_readiness()
        self.assertTrue(
            batch.issue_ids.filtered(lambda issue: issue.kind == "product_tax_unclear"),
        )
        batch.action_settle_product_taxes()
        self.assertEqual(len(jersey.taxes_id), 1)
        self.assertFalse(
            batch.issue_ids.filtered(lambda issue: issue.kind == "product_tax_unclear"),
        )

    def test_a_position_stating_a_retired_rate_is_reported(self):
        retired = self._tax("Invented retired 0% export", 0, self.france)
        position = self.env["account.fiscal.position"].search(
            [("company_id", "=", self.company.id), ("name", "=", "Invented export")],
        )
        retired.original_tax_ids = [(6, 0, self.home_tax.ids)]
        position.tax_ids = [(4, retired.id)]
        retired.active = False
        batch, _jersey, _cap = self._etsy_drop()
        batch.action_check_readiness()
        issue = batch.issue_ids.filtered(lambda item: item.kind == "retired_rate")
        self.assertTrue(issue)
        self.assertIn("Invented retired 0% export", issue[0].note)

    def test_carriage_without_a_product_is_reported(self):
        self.channels["etsy"].shipping_product_id = False
        batch, _jersey, _cap = self._etsy_drop()
        batch.action_check_readiness()
        self.assertTrue(
            batch.issue_ids.filtered(lambda issue: issue.kind == "carriage_unaccounted"),
        )

    def test_revenue_reaching_an_archived_account_is_reported(self):
        """The account an invoice would use has to be one it can post to.

        A chart keeps its retired accounts, and a product still points at one
        long after it stopped being usable; the invoice only says so when it is
        written, which is far too late.
        """
        batch, jersey, _cap = self._etsy_drop()
        retired = self._account("Invented retired revenue", "income")
        jersey.product_tmpl_id.property_account_income_id = retired
        retired.active = False
        batch._check_revenue_accounts()
        issue = batch.issue_ids.filtered(
            lambda item: item.kind == "revenue_unaccounted",
        )
        self.assertTrue(
            issue,
            msg=f"resolved to {batch._income_account(jersey)} "
                f"active={batch._income_account(jersey).active}",
        )
        self.assertIn("archived", issue[0].name)

    def test_revenue_with_no_account_at_all_is_reported(self):
        batch, _jersey, _cap = self._etsy_drop()
        with patch(
            "odoo.addons.usl_b2c_ingest.models.readiness"
            ".B2cImportBatchReadiness._income_account",
            return_value=self.env["account.account"],
        ):
            batch._check_revenue_accounts()
        issue = batch.issue_ids.filtered(
            lambda item: item.kind == "revenue_unaccounted",
        )
        self.assertTrue(issue)
        self.assertIn("no income account", issue[0].name)

    def test_an_operator_that_would_not_reverse_charge_is_reported(self):
        batch, _jersey, _cap = self._etsy_drop()
        batch.correct_chart = False
        batch.action_check_readiness()
        issue = batch.issue_ids.filtered(lambda item: item.kind == "operator_untaxed")
        self.assertTrue(issue)
        self.assertIn("no tax number", issue[0].note)

    def test_the_chart_is_put_right_before_it_is_reported_on(self):
        """The default is to correct, because a defect put right is not news.

        Most of what the checks would report is a defect in the chart rather
        than in the drop, and reporting a thing that can be put right without
        putting it right wastes the reader's attention.
        """
        shadow = self._position("Invented catch-all", False, self.home_tax, sequence=1)
        batch, _jersey, _cap = self._etsy_drop()
        self.assertTrue(batch.correct_chart)
        batch.action_check_readiness()
        self.assertFalse(shadow.active)
        self.assertFalse(
            batch.issue_ids.filtered(lambda issue: issue.kind == "shadowed_destination"),
        )

    def test_the_chart_is_left_alone_when_that_is_asked_for(self):
        shadow = self._position("Invented catch-all", False, self.home_tax, sequence=1)
        batch, _jersey, _cap = self._etsy_drop()
        batch.correct_chart = False
        batch.action_check_readiness()
        self.assertTrue(shadow.active)

    # -- goods that have not left yet --------------------------------------

    def _unshipped_drop(self):
        """Return a drop whose first order is paid and not yet shipped."""
        self._etsy_drop()
        rows = ({**fixtures.ETSY_ORDER_ROWS[0], "Date Shipped": ""},)
        batch = self._batch(
            **{
                "etsy-orders.csv": fixtures.etsy_orders(rows),
                "etsy-items.csv": fixtures.etsy_order_items(
                    ({**fixtures.ETSY_ITEM_ROWS[0], "Date Shipped": ""},),
                ),
            },
        )
        batch.action_parse()
        batch.action_resolve()
        batch.action_apply()
        return batch

    def test_money_for_goods_that_have_not_left_is_not_revenue_yet(self):
        batch = self._unshipped_drop()
        batch.action_invoice()
        self.assertFalse(batch.invoiced_move_ids, "an unshipped order is not a sale yet")
        self.assertEqual(len(batch.advance_payment_ids), 1)
        advance = batch.advance_payment_ids
        self.assertAlmostEqual(advance.amount, 45.00, places=2)
        # The money is in the clearing account and owed back to the customer,
        # so no revenue and no VAT has been stated.
        receivable = advance.move_id.line_ids.filtered(
            lambda line: line.account_id.account_type == "asset_receivable",
        )
        self.assertAlmostEqual(receivable.balance, -45.00, places=2)
        self.assertFalse(advance.move_id.line_ids.filtered(lambda line: line.tax_line_id))

    def test_the_advance_becomes_the_invoice_when_the_goods_go_out(self):
        held = self._unshipped_drop()
        held.action_invoice()
        advance = held.advance_payment_ids

        shipped = self._batch(
            **{
                "etsy-orders.csv": fixtures.etsy_orders((fixtures.ETSY_ORDER_ROWS[0],)),
                "etsy-items.csv": fixtures.etsy_order_items((fixtures.ETSY_ITEM_ROWS[0],)),
            },
        )
        shipped.action_parse()
        shipped.action_resolve()
        shipped.action_apply()
        shipped.action_reconcile()
        shipped.action_invoice()

        invoice = shipped.invoiced_move_ids
        self.assertEqual(len(invoice), 1)
        self.assertEqual(invoice.payment_state, "paid")
        self.assertEqual(advance.state, "reconciled")
        # Nothing was collected twice: the customer owes nothing and the
        # clearing account holds exactly the one receipt.
        self.assertAlmostEqual(invoice.amount_residual, 0.00, places=2)
        self.assertEqual(
            self.env["account.payment"].search_count(
                [("company_id", "=", self.company.id), ("partner_type", "=", "customer")],
            ),
            1,
        )

    def test_holding_an_advance_twice_holds_it_once(self):
        batch = self._unshipped_drop()
        batch.action_invoice()
        first = batch.advance_payment_ids
        batch.action_invoice()
        self.assertEqual(batch.advance_payment_ids, first)

    def test_an_unshipped_order_still_delivers_nothing(self):
        batch = self._unshipped_drop()
        sale = self._sale(batch, "9000000001")
        self.assertFalse(sale.picking_ids.filtered(lambda item: item.state == "done"))
