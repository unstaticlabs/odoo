"""What a channel says became of an order, and what Odoo does about it."""

from odoo.tests import tagged

from . import fixtures
from .test_accounting import TestAccounting
from odoo.addons.usl_b2c_ingest.models.lifecycle import (
    CANCELLED,
    PART_REFUNDED,
    REFUNDED,
    SOLD,
    stated_outcome,
)


def _orders(**changes):
    """Return the Etsy order rows with the first order restated."""
    first = dict(fixtures.ETSY_ORDER_ROWS[0], **changes)
    return (first, *fixtures.ETSY_ORDER_ROWS[1:])


@tagged("post_install", "-at_install", "usl_b2c_ingest")
class TestStatedOutcome(TestAccounting):
    """A channel's own word for an order, read as what became of it."""

    def test_a_word_no_channel_uses_is_a_sale_that_stands(self):
        self.assertEqual(stated_outcome({"original_provider_state": "Shipped"})[0], SOLD)
        self.assertEqual(stated_outcome({"original_provider_state": ""})[0], SOLD)
        self.assertEqual(stated_outcome({})[0], SOLD)

    def test_the_words_channels_do_use_are_read(self):
        self.assertEqual(stated_outcome({"original_provider_state": "Canceled"})[0], CANCELLED)
        self.assertEqual(stated_outcome({"source_payment_state": "refunded"})[0], REFUNDED)
        self.assertEqual(
            stated_outcome({"source_payment_state": "partially refunded"})[0],
            PART_REFUNDED,
        )

    def test_two_exports_settle_on_the_one_that_took_most_back(self):
        outcome, _amount = stated_outcome(
            {"original_provider_state": "Canceled", "source_payment_state": "refunded"},
        )
        self.assertEqual(outcome, CANCELLED)

    def test_an_adjusted_total_is_a_refund_a_channel_never_names(self):
        outcome, given_back = stated_outcome(
            {"buyer_paid_amount": "45.00", "adjusted_total_amount": "38.89"},
        )
        self.assertEqual(outcome, PART_REFUNDED)
        self.assertAlmostEqual(float(given_back), 6.11, places=2)

    def test_an_order_nothing_happened_to_carries_no_adjustment(self):
        outcome, given_back = stated_outcome(
            {
                "buyer_paid_amount": "45.00",
                "adjusted_total_amount": "0",
                "adjusted_fee_amount": "0",
                "adjusted_net_amount": "0",
            },
        )
        self.assertEqual(outcome, SOLD)
        self.assertIsNone(given_back)

    def test_an_adjustment_to_nothing_is_a_refund_in_full(self):
        outcome, given_back = stated_outcome(
            {
                "buyer_paid_amount": "45.00",
                "adjusted_total_amount": "0",
                "adjusted_net_amount": "-43.00",
            },
        )
        self.assertEqual(outcome, REFUNDED)
        self.assertAlmostEqual(float(given_back), 45.00, places=2)


@tagged("post_install", "-at_install", "usl_b2c_ingest")
class TestLifecycle(TestAccounting):
    """A second drop reconciles Odoo to the channel rather than repeating it."""

    def _drop(self, orders):
        batch = self._batch(
            **{
                "etsy-orders.csv": fixtures.etsy_orders(orders),
                "etsy-items.csv": fixtures.etsy_order_items(),
            },
        )
        batch.action_parse()
        batch.action_resolve()
        batch.action_apply()
        return batch

    def _clearing_balance(self):
        return round(
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
        )

    # -- an order that never completed -------------------------------------

    def test_a_cancelled_order_never_becomes_a_sale(self):
        self._etsy_drop()
        batch = self._drop(_orders(Status="Canceled"))
        self.assertEqual(batch.applied_sale_ids.mapped("client_order_ref"), ["9000000002"])
        self.assertFalse(
            self.env["b2c.order"].search(
                [("company_id", "=", self.company.id), ("external_order_id", "=", "9000000001")],
            ),
        )

    def test_an_order_cancelled_before_it_shipped_cancels_the_sale_it_became(self):
        # Paid, not yet shipped, and cancelled the next day: the money is held
        # as an advance, so cancelling has to give it back as well.
        self._etsy_drop()
        batch = self._drop(_orders(**{"Date Shipped": ""}))
        batch.action_invoice()
        sale = self._sale(batch, "9000000001")
        self.assertEqual(sale.state, "sale")
        self.assertTrue(batch.advance_payment_ids)

        later = self._drop(_orders(**{"Date Shipped": "", "Status": "Canceled"}))
        later.action_reconcile()
        self.assertEqual(sale.state, "cancel")
        self.assertEqual(later.cancelled_sale_ids, sale)
        # Nothing of the customer's is still held: what came in went back out.
        self.assertFalse(
            self.env["account.move.line"].search(
                [
                    ("company_id", "=", self.company.id),
                    ("partner_id", "=", sale.partner_id.id),
                    ("account_id.account_type", "=", "asset_receivable"),
                    ("parent_state", "=", "posted"),
                    ("reconciled", "=", False),
                ],
            ),
        )

    def test_an_order_cancelled_after_it_shipped_is_reported(self):
        batch, _jersey, _cap = self._etsy_drop()
        batch.action_apply()
        batch.action_invoice()
        sale = self._sale(batch, "9000000001")

        later = self._drop(_orders(Status="Canceled"))
        later.action_reconcile()
        # The goods went out, so what is owed back is a credit note, and
        # whether the goods came back is not this drop's to say.
        self.assertNotEqual(sale.state, "cancel")
        self.assertTrue(later.credit_note_ids)
        self.assertIn("cancelled_after_delivery", later.issue_ids.mapped("kind"))

    # -- an order that was given back --------------------------------------

    def test_a_refunded_order_is_credited_and_paid_back(self):
        batch, _jersey, _cap = self._etsy_drop()
        batch.action_apply()
        batch.action_invoice()
        held = self._clearing_balance()

        later = self._drop(_orders(Status="Refunded"))
        later.action_reconcile()
        credit = later.credit_note_ids
        self.assertEqual(len(credit), 1)
        self.assertEqual(credit.move_type, "out_refund")
        self.assertEqual(credit.state, "posted")
        self.assertEqual(credit.payment_state, "paid")
        # What was collected went back out of the same clearing account.
        self.assertAlmostEqual(self._clearing_balance(), held - 45.00, places=2)

    def test_reconciling_twice_credits_once(self):
        batch, _jersey, _cap = self._etsy_drop()
        batch.action_apply()
        batch.action_invoice()
        later = self._drop(_orders(Status="Refunded"))
        later.action_reconcile()
        first = later.credit_note_ids
        later.action_reconcile()
        self.assertEqual(later.credit_note_ids, first)
        self.assertEqual(
            self.env["account.move"].search_count(
                [("company_id", "=", self.company.id), ("move_type", "=", "out_refund")],
            ),
            1,
        )

    def test_a_refund_before_anything_was_invoiced_undoes_the_sale(self):
        # Paid, never shipped, refunded: nothing was ever a sale, so there is
        # nothing to credit — only money to give back.
        self._etsy_drop()
        batch = self._drop(_orders(**{"Date Shipped": ""}))
        batch.action_invoice()
        sale = self._sale(batch, "9000000001")

        later = self._drop(_orders(**{"Date Shipped": "", "Status": "Refunded"}))
        later.action_reconcile()
        self.assertEqual(sale.state, "cancel")
        self.assertFalse(later.credit_note_ids, "nothing was invoiced to credit")

    def test_a_part_refund_is_drafted_and_left_to_a_person(self):
        batch, _jersey, _cap = self._etsy_drop()
        batch.action_apply()
        batch.action_invoice()

        later = self._drop(
            _orders(**{"Adjusted Order Total": "38.89", "Adjusted Net Order Amount": "36.89"}),
        )
        later.action_reconcile()
        credit = later.credit_note_ids
        self.assertEqual(credit.state, "draft", "a guess must never post itself")
        finding = later.issue_ids.filtered(
            lambda issue: issue.kind == "part_refund_unallocated",
        )
        self.assertEqual(finding.severity, "advisory")
        self.assertIn("6.11", finding.note)
