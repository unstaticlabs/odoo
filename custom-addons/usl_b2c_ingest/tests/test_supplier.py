"""Tying the supplier's fulfilment to the sale it fulfilled."""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import tagged

from . import fixtures
from .test_import_batch import TestImportBatch
from odoo.addons.usl_b2c_ingest.models.supplier import MATCH_WINDOW

MODEL = "odoo.addons.usl_b2c_ingest.models.supplier.B2cImportBatchFulfilment"


class _Supplier:
    """Stands in for Printful so a test never reaches the network."""

    def __init__(self, orders):
        self._orders = orders

    def orders(self):
        return iter(self._orders)


@tagged("post_install", "-at_install", "usl_b2c_ingest")
class TestSupplier(TestImportBatch):
    """A fulfilment is tied to a sale by evidence, or reported as untied."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.channels["etsy"].printful_store_id = "11111111"

    def _read(self, orders, files=None):
        batch = self._batch(
            **(
                files
                or {
                    "etsy-orders.csv": fixtures.etsy_orders(),
                    "etsy-items.csv": fixtures.etsy_order_items(),
                }
            ),
        )
        batch.action_parse()
        with patch(f"{MODEL}._printful_client", return_value=_Supplier(orders)):
            batch.action_fetch_fulfilment()
        return batch

    def _fulfilment(self, batch, printful_id):
        return batch._fulfilment_rows().filtered(
            lambda row: (row.values or {}).get("printful_order_id") == str(printful_id),
        )

    def _sale(self, batch, external_order_id):
        return batch.row_ids.filtered(
            lambda row: row.grain == "order"
            and row.resolution != "supplier"
            and row.external_order_id == external_order_id,
        )

    def test_a_stated_reference_ties_the_fulfilment_to_its_sale(self):
        batch = self._read(fixtures.PRINTFUL_ORDERS)
        fulfilment = self._fulfilment(batch, 5000000001)
        self.assertEqual(fulfilment.fulfilment_of_row_id, self._sale(batch, "9000000001"))
        self.assertIn("order reference", fulfilment.fulfilment_evidence)

    def test_the_channel_a_store_fulfils_for_is_configuration(self):
        batch = self._read(fixtures.PRINTFUL_ORDERS)
        self.assertEqual(self._fulfilment(batch, 5000000001).provider, "etsy")
        self.channels["etsy"].printful_store_id = False
        batch = self._read(fixtures.PRINTFUL_ORDERS)
        self.assertEqual(self._fulfilment(batch, 5000000001).provider, "printful")

    def test_a_silent_reference_is_proved_from_the_recipient_and_the_date(self):
        order = dict(fixtures.PRINTFUL_ORDERS[0])
        order["external_id"] = None
        order["recipient"] = dict(order["recipient"], email="two@example.invalid")
        order["created_at"] = "2026-03-10T09:00:00Z"
        batch = self._batch(
            **{
                "medusa-items.csv": fixtures.medusa_order_items(),
                "etsy-orders.csv": fixtures.etsy_orders(),
            },
        )
        batch.action_parse()
        with patch(f"{MODEL}._printful_client", return_value=_Supplier([order])):
            batch.action_fetch_fulfilment()
        fulfilment = self._fulfilment(batch, 5000000001)
        self.assertEqual(fulfilment.fulfilment_of_row_id, self._sale(batch, "8000000002"))
        self.assertIn("Same recipient", fulfilment.fulfilment_evidence)

    def test_a_recipient_too_far_from_the_sale_proves_nothing(self):
        order = dict(fixtures.PRINTFUL_ORDERS[0])
        order["external_id"] = None
        order["recipient"] = dict(order["recipient"], email="two@example.invalid")
        order["created_at"] = "2026-06-01T09:00:00Z"
        batch = self._batch(**{"medusa-items.csv": fixtures.medusa_order_items()})
        batch.action_parse()
        with patch(f"{MODEL}._printful_client", return_value=_Supplier([order])):
            batch.action_fetch_fulfilment()
        self.assertFalse(self._fulfilment(batch, 5000000001).fulfilment_of_row_id)

    def test_a_fulfilment_that_ties_to_no_sale_is_reported(self):
        order = dict(fixtures.PRINTFUL_ORDERS[0], external_id="unknown-reference")
        batch = self._read([order])
        self.assertIn(
            "fulfilment_unmatched",
            {issue.kind for issue in batch.issue_ids},
        )

    def test_a_fulfilment_older_than_the_drop_is_not_reported(self):
        order = dict(
            fixtures.PRINTFUL_ORDERS[0],
            external_id="unknown-reference",
            created_at="2024-01-01T09:00:00Z",
        )
        batch = self._read([order])
        self.assertNotIn(
            "fulfilment_unmatched",
            {issue.kind for issue in batch.issue_ids},
        )

    def test_shipping_is_taken_only_from_a_fulfilment_of_the_whole_order(self):
        batch = self._read(fixtures.PRINTFUL_ORDERS)
        sale = self._sale(batch, "9000000001")
        self.assertEqual(batch._evidenced_shipping(sale, Decimal("40.00")), Decimal("5.00"))
        # The same fulfilment says nothing about an order it only partly shipped.
        self.assertIsNone(batch._evidenced_shipping(sale, Decimal("70.00")))

    def test_the_supplier_cost_of_each_line_is_kept(self):
        batch = self._read(fixtures.PRINTFUL_ORDERS)
        sale = self._sale(batch, "9000000001")
        costs = batch._supplier_costs()[sale.id]
        self.assertEqual(len(costs), 1)
        self.assertEqual(costs[0]["unit_cost"], Decimal("16.00"))
        self.assertEqual(costs[0]["quantity"], Decimal("1"))

    def test_fulfilments_are_evidence_about_orders_not_orders(self):
        before = self._batch(**{"etsy-orders.csv": fixtures.etsy_orders()})
        before.action_parse()
        counted = before.order_count
        batch = self._read(fixtures.PRINTFUL_ORDERS)
        self.assertEqual(batch.order_count, counted)
        self.assertEqual(batch.fulfilment_count, 1)

    def test_reading_the_supplier_twice_keeps_one_copy(self):
        batch = self._read(fixtures.PRINTFUL_ORDERS)
        first = (batch.fulfilment_count, batch.row_count)
        with patch(f"{MODEL}._printful_client", return_value=_Supplier(fixtures.PRINTFUL_ORDERS)):
            batch.action_fetch_fulfilment()
        self.assertEqual((batch.fulfilment_count, batch.row_count), first)

    def test_reading_the_supplier_needs_a_token(self):
        batch = self._batch(**{"etsy-orders.csv": fixtures.etsy_orders()})
        batch.action_parse()
        self.env["ir.config_parameter"].sudo().set_str(
            "usl_b2c_ingest.printful_token", "",
        )
        with self.assertRaises(UserError):
            batch.action_fetch_fulfilment()

    def test_a_window_of_ten_days_is_what_the_recipient_evidence_allows(self):
        self.assertEqual(MATCH_WINDOW, timedelta(days=10))
