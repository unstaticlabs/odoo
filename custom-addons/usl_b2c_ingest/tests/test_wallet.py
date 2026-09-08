"""Settling what the supplier drew on the wallet it is paid from."""

from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import tagged

from . import fixtures
from .test_accounting import TestAccounting
from .test_supplier import MODEL, _Supplier

#: A Printful order for a store no channel claims: nobody sold it, so nobody
#: is owed for it, but the wallet paid for it all the same.
INTERNAL_ORDER = {
    "id": 5000000050,
    "external_id": None,
    "store_id": "99999999",
    "status": "fulfilled",
    "created_at": "2026-03-14T09:00:00Z",
    "recipient": {
        "name": "Invented Studio",
        "address1": "3 Sample Road",
        "city": "Lille",
        "zip": "59000",
        "country_code": "FR",
        "email": "studio@example.invalid",
    },
    "costs": {
        "currency": "EUR",
        "subtotal": "8.00",
        "discount": "0.00",
        "shipping": "2.00",
        "tax": "0.00",
        "vat": "0.00",
        "total": "10.00",
    },
    "order_items": [],
}


@tagged("post_install", "-at_install", "usl_b2c_ingest")
class TestWallet(TestAccounting):
    """What the supplier drew leaves the wallet, once, in the month it drew it."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.channels["etsy"].printful_store_id = "11111111"
        cls.wallet_account = cls._account("Invented supplier wallet", "asset_current")
        cls.cost_of_sales = cls._account("Invented cost of goods sold", "expense")
        cls.prototyping = cls._account("Invented prototyping", "expense")
        cls.wallet_journal = cls.env["account.journal"].create(
            {
                "name": "Invented wallet",
                "code": "INVW",
                "type": "bank",
                "company_id": cls.company.id,
                "default_account_id": cls.wallet_account.id,
            },
        )
        (
            cls.wallet_journal.inbound_payment_method_line_ids
            | cls.wallet_journal.outbound_payment_method_line_ids
        ).payment_account_id = cls.wallet_account
        cls.supplier = cls.env["res.partner"].create(
            {"name": "Invented Supplier", "country_id": cls.env.ref("base.lv").id},
        )
        cls.company.write(
            {
                "usl_b2c_supply_partner_id": cls.supplier.id,
                "usl_b2c_wallet_journal_id": cls.wallet_journal.id,
                "usl_b2c_supply_product_id": cls._supply_product(
                    "Invented supply of goods sold", cls.cost_of_sales,
                ).id,
                "usl_b2c_internal_supply_product_id": cls._supply_product(
                    "Invented supply for no sale", cls.prototyping,
                ).id,
            },
        )

    @classmethod
    def _supply_product(cls, name, account):
        return cls.env["product.product"].create(
            {
                "name": name,
                "type": "service",
                "purchase_ok": True,
                "supplier_taxes_id": [(5, 0, 0)],
                "property_account_expense_id": account.id,
            },
        )

    def _top_up(self, amount):
        """Pay into the wallet the way a bank statement does."""
        bank = self._account("Invented bank", "asset_cash")
        move = self.env["account.move"].create(
            {
                "company_id": self.company.id,
                "journal_id": self.env["account.journal"].search(
                    [("company_id", "=", self.company.id), ("type", "=", "general")], limit=1,
                ).id,
                "date": "2026-03-01",
                "line_ids": [
                    (0, 0, {"account_id": self.wallet_account.id, "balance": amount}),
                    (0, 0, {"account_id": bank.id, "balance": -amount}),
                ],
            },
        )
        move.action_post()
        return move

    def _drop(self, orders=fixtures.PRINTFUL_ORDERS):
        batch, _jersey, _cap = self._etsy_drop()
        with patch(f"{MODEL}._printful_client", return_value=_Supplier(orders)):
            batch.action_fetch_fulfilment()
        batch.action_apply()
        return batch

    def _refund_drop(self):
        """Return a drop that carries the supplier's word and nothing else."""
        later = self.env["b2c.import.batch"].create(
            {"name": "Refund", "company_id": self.company.id},
        )
        later.state = "resolved"
        with patch(
            f"{MODEL}._printful_client",
            return_value=_Supplier(fixtures.PRINTFUL_REFUNDED),
        ):
            later.action_fetch_fulfilment()
        later.action_apply()
        return later

    def _wallet_balance(self):
        return round(
            sum(
                self.env["account.move.line"].search(
                    [
                        ("company_id", "=", self.company.id),
                        ("account_id", "=", self.wallet_account.id),
                        ("parent_state", "=", "posted"),
                    ],
                ).mapped("balance"),
            ),
            2,
        )

    # -- what the supplier drew --------------------------------------------

    def test_what_the_supplier_drew_leaves_the_wallet(self):
        self._top_up(100.00)
        batch = self._drop()
        batch.action_settle_wallet()
        bill = batch.wallet_move_ids
        self.assertEqual(len(bill), 1)
        self.assertEqual(bill.move_type, "in_invoice")
        self.assertEqual(bill.state, "posted")
        self.assertEqual(bill.payment_state, "paid")
        self.assertEqual(bill.partner_id, self.supplier)
        self.assertAlmostEqual(bill.amount_total, 20.00, places=2)
        # A month's supply is one document, dated the month it was drawn in.
        self.assertEqual(str(bill.invoice_date), "2026-03-31")
        self.assertEqual(bill.ref, "printful:wallet:2026-03:EUR")
        self.assertAlmostEqual(self._wallet_balance(), 80.00, places=2)

    def test_the_bill_states_the_same_cost_the_margin_does(self):
        self._top_up(100.00)
        batch = self._drop()
        batch.action_settle_wallet()
        goods = self._sale(batch, "9000000001").order_line.filtered(
            lambda line: line.product_id != self.carriage,
        )
        cost = self._balances(batch.wallet_move_ids).get(self.cost_of_sales, 0)
        self.assertAlmostEqual(cost, goods.purchase_price * goods.product_uom_qty, places=2)

    def test_supply_answering_to_no_sale_is_never_a_cost_of_sales(self):
        self._top_up(100.00)
        batch = self._drop((*fixtures.PRINTFUL_ORDERS, INTERNAL_ORDER))
        batch.action_settle_wallet()
        balances = self._balances(batch.wallet_move_ids)
        self.assertAlmostEqual(balances[self.cost_of_sales], 20.00, places=2)
        self.assertAlmostEqual(balances[self.prototyping], 10.00, places=2)
        self.assertAlmostEqual(self._wallet_balance(), 70.00, places=2)

    def test_settling_twice_bills_once(self):
        self._top_up(100.00)
        batch = self._drop()
        batch.action_settle_wallet()
        first = batch.wallet_move_ids
        batch.action_settle_wallet()
        self.assertEqual(batch.wallet_move_ids, first)
        self.assertAlmostEqual(self._wallet_balance(), 80.00, places=2)

    def test_a_refund_afterwards_puts_it_back_in_the_wallet(self):
        self._top_up(100.00)
        batch = self._drop()
        batch.action_settle_wallet()
        self.assertAlmostEqual(self._wallet_balance(), 80.00, places=2)

        # The supplier gives it back a fortnight later, in a drop about the
        # refund: it holds no sale of its own, only the cost that changed.
        later = self._refund_drop()
        later.action_settle_wallet()
        credit = later.wallet_move_ids.filtered(lambda move: move.move_type == "in_refund")
        self.assertTrue(credit, "a supplier that gives back is credited, not billed again")
        self.assertAlmostEqual(credit.amount_total, 20.00, places=2)
        # Charged 20.00 and given 20.00 back: the wallet is whole again.
        self.assertAlmostEqual(self._wallet_balance(), 100.00, places=2)

    def test_an_overdrawn_wallet_is_reported(self):
        batch = self._drop()
        batch.action_settle_wallet()
        finding = batch.issue_ids.filtered(lambda issue: issue.kind == "wallet_overdrawn")
        self.assertEqual(finding.severity, "advisory")
        self.assertAlmostEqual(self._wallet_balance(), -20.00, places=2)

    def test_a_wallet_nobody_configured_says_so(self):
        self.company.usl_b2c_wallet_journal_id = False
        batch = self._drop()
        with self.assertRaisesRegex(UserError, "wallet journal"):
            batch.action_settle_wallet()

    def _ledger_shape(self):
        """Return everything a second run must leave exactly as it found it."""
        counted = {
            model: self.env[model].search_count([("company_id", "=", self.company.id)])
            for model in (
                "account.move",
                "account.payment",
                "sale.order",
                "b2c.order",
                "b2c.fulfilment.event",
                "b2c.supply.settlement",
            )
        }
        return {
            **counted,
            "wallet": self._wallet_balance(),
            "clearing": round(
                sum(
                    self.env["account.move.line"].search(
                        [
                            ("company_id", "=", self.company.id),
                            ("account_id", "=", self.clearing.id),
                            ("parent_state", "=", "posted"),
                        ],
                    ).mapped("balance"),
                ),
                2,
            ),
        }

    def test_running_the_whole_drop_twice_changes_nothing(self):
        self._top_up(100.00)
        batch = self._drop((*fixtures.PRINTFUL_ORDERS, INTERNAL_ORDER))

        def run():
            batch.action_apply()
            batch.action_reconcile()
            batch.action_invoice()
            batch.action_bill_fees()
            batch.action_settle_wallet()

        run()
        first = self._ledger_shape()
        run()
        self.assertEqual(self._ledger_shape(), first)

    def test_what_each_document_settled_is_recorded(self):
        self._top_up(100.00)
        batch = self._drop()
        batch.action_settle_wallet()
        settlement = self.env["b2c.supply.settlement"].search(
            [("company_id", "=", self.company.id)],
        )
        self.assertEqual(len(settlement), 1)
        self.assertEqual(settlement.move_id, batch.wallet_move_ids)
        self.assertEqual(settlement.batch_id, batch)
        self.assertAlmostEqual(settlement.cogs_amount, 20.00, places=2)
        self.assertEqual(settlement.event_id.accounting_link_state, "verified")
