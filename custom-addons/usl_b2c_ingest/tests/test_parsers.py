"""The parsers, exercised without a database."""

from decimal import Decimal

from odoo.tests import TransactionCase, tagged

from . import fixtures
from odoo.addons.usl_b2c_ingest import parsers
from odoo.addons.usl_b2c_ingest.parsers import printful


@tagged("post_install", "-at_install", "usl_b2c_ingest")
class TestParsers(TransactionCase):
    """Format recognition and the identities each channel's numbers obey."""

    def _parse(self, content):
        fmt = parsers.detect(content)
        rows, document = parsers.parse(fmt, "fixture.csv", content)
        return fmt, rows, document

    def _by_grain(self, rows, grain):
        return [row for row in rows if row.grain == grain]

    def test_every_shape_is_recognised_by_its_columns(self):
        expected = {
            fixtures.etsy_orders(): "etsy_orders",
            fixtures.etsy_order_items(): "etsy_order_items",
            fixtures.medusa_orders(): "medusa_orders",
            fixtures.medusa_order_items(): "medusa_order_items",
        }
        for content, format_id in expected.items():
            self.assertEqual(parsers.detect(content).format_id, format_id)

    def test_a_reordered_export_is_still_recognised(self):
        header = list(parsers.FORMATS_BY_ID["etsy_orders"].header)
        reordered = tuple(reversed(header))
        content = fixtures._csv(reordered, fixtures.ETSY_ORDER_ROWS)
        fmt, rows, _document = self._parse(content)
        self.assertEqual(fmt.format_id, "etsy_orders")
        self.assertEqual(len(rows), len(fixtures.ETSY_ORDER_ROWS))

    def test_a_truncated_export_names_the_columns_it_lost(self):
        header = list(parsers.FORMATS_BY_ID["etsy_orders"].header)[:-2]
        with self.assertRaises(parsers.SchemaError) as caught:
            parsers.detect(fixtures._csv(header, fixtures.ETSY_ORDER_ROWS))
        message = str(caught.exception)
        self.assertIn("Etsy — sold orders", message)
        self.assertIn("InPerson Location", message)
        self.assertIn("SKU", message)

    def test_etsy_separates_what_the_shop_earns_from_what_the_marketplace_keeps(self):
        _fmt, rows, _document = self._parse(fixtures.etsy_orders())
        orders = {row.external_order_id: row.values for row in rows}

        european = orders["9000000001"]
        self.assertEqual(european["total_amount"], Decimal("45.00"))
        self.assertEqual(european["marketplace_collected_tax"], Decimal("0.00"))

        american = orders["9000000002"]
        # 70.00 sold, 7.00 discounted, 6.00 shipping: the shop's own 69.00.
        self.assertEqual(american["total_amount"], Decimal("69.00"))
        # The buyer paid 73.00. The extra 4.00 is the marketplace's own tax.
        self.assertEqual(american["marketplace_collected_tax"], Decimal("4.00"))
        self.assertEqual(american["fee_amount"], Decimal("3.00"))
        self.assertEqual(american["net_amount"], Decimal("66.00"))

    def test_etsy_net_identity_is_measured_rather_than_assumed(self):
        broken = [dict(fixtures.ETSY_ORDER_ROWS[0]) | {"Order Net": "40.00"}]
        _fmt, rows, _document = self._parse(fixtures.etsy_orders(broken))
        self.assertEqual(rows[0].values["net_identity_residual"], Decimal("-3.00"))

    def test_etsy_order_money_is_read_as_context_not_as_a_line_amount(self):
        _fmt, rows, _document = self._parse(fixtures.etsy_order_items())
        lines = {row.external_line_id: row.values for row in rows}
        self.assertEqual(lines["7000000002"]["quantity"], Decimal("2"))
        self.assertEqual(lines["7000000002"]["unit_price"], Decimal("35.00"))
        self.assertEqual(lines["7000000002"]["subtotal_amount"], Decimal("70"))
        self.assertEqual(lines["7000000002"]["order_shipping_amount"], Decimal("6.00"))

    def test_html_escaping_in_a_name_is_resolved(self):
        _fmt, rows, _document = self._parse(fixtures.etsy_orders())
        names = {row.values["customer_name"] for row in rows}
        self.assertIn("Cormac O'Placeholder", names)

    def test_both_medusa_layouts_state_the_same_lines(self):
        _fmt, wide, _document = self._parse(fixtures.medusa_orders())
        _fmt, long, _document = self._parse(fixtures.medusa_order_items())
        by_key = {
            layout: {
                (row.external_order_id, row.external_line_id): row.values
                for row in self._by_grain(rows, parsers.LINE_GRAIN)
            }
            for layout, rows in (("wide", wide), ("long", long))
        }
        self.assertEqual(by_key["wide"], by_key["long"])
        self.assertEqual(len(by_key["wide"]), 4)

    def test_a_repeated_line_keeps_two_identities(self):
        _fmt, rows, _document = self._parse(fixtures.medusa_order_items())
        repeated = [
            row.external_line_id
            for row in self._by_grain(rows, parsers.LINE_GRAIN)
            if row.external_order_id == "8000000002"
        ]
        self.assertEqual(len(set(repeated)), 2)
        self.assertTrue(any(identity.endswith("#2") for identity in repeated))

    def test_medusa_announces_each_order_once(self):
        _fmt, rows, _document = self._parse(fixtures.medusa_order_items())
        orders = self._by_grain(rows, parsers.ORDER_GRAIN)
        self.assertEqual([row.external_order_id for row in orders], ["8000000001", "8000000002"])
        self.assertEqual(orders[1].values["currency"], "GBP")

    def test_medusa_carries_no_order_money(self):
        _fmt, rows, _document = self._parse(fixtures.medusa_order_items())
        order = self._by_grain(rows, parsers.ORDER_GRAIN)[0]
        self.assertNotIn("total_amount", order.values)
        self.assertNotIn("shipping_amount", order.values)

    def test_printful_costs_are_read_against_the_marketplace_order(self):
        rows = list(printful.parse_orders(fixtures.PRINTFUL_ORDERS, {"11111111": ("etsy", "sale")}))
        order, line = rows
        self.assertEqual(order.provider, "etsy")
        self.assertEqual(order.external_order_id, "9000000001")
        self.assertEqual(order.values["costs_total"], Decimal("20.00"))
        self.assertEqual(order.values["retail_costs_total"], Decimal("45.00"))
        self.assertEqual(line.values["supplier_unit_cost"], Decimal("16.00"))
        self.assertEqual(line.values["unit_price"], Decimal("40.00"))

    def test_an_unmapped_printful_store_is_not_guessed_into_a_channel(self):
        rows = list(printful.parse_orders(fixtures.PRINTFUL_ORDERS, {}))
        self.assertEqual(rows[0].provider, "printful")
        self.assertEqual(rows[0].values["business_purpose"], "")
