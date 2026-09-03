from odoo.tests import BaseCase, tagged

from odoo.addons.usl_b2c_restore.parsers import (
    normalize_printful_order_reference,
    parse_legacy_delivery_address,
)


@tagged("post_install", "-at_install")
class TestNativeHistoryParserNormalization(BaseCase):
    def test_printful_reference_prefixes_and_display_whitespace(self):
        self.assertEqual(
            normalize_printful_order_reference("Order #1617586251"),
            "1617586251",
        )
        self.assertEqual(
            normalize_printful_order_reference(
                "order_01KWQF6Y14CCS8WSGGDNFF BXT1",
            ),
            "order_01KWQF6Y14CCS8WSGGDNFFBXT1",
        )
        self.assertEqual(
            normalize_printful_order_reference(
                "Refund to wallet #order_01ABC",
            ),
            "order_01ABC",
        )

    def test_legacy_addresses_preserve_full_delivery_identity(self):
        french = parse_legacy_delivery_address(
            "**** ****, *** **** ***, ****, Nice, 06200, FR",
        )
        self.assertEqual(french["shipping_name"], "**** ****")
        self.assertEqual(french["shipping_street"], "*** **** ***")
        self.assertEqual(french["shipping_street2"], "****")
        self.assertEqual(french["shipping_city"], "Nice")
        self.assertEqual(french["shipping_zip"], "06200")
        self.assertEqual(french["country"], "FR")

        american = parse_legacy_delivery_address(
            "**** ****, *** **** ***, ROSWELL, GA 30076, US",
        )
        self.assertEqual(american["shipping_city"], "ROSWELL")
        self.assertEqual(american["shipping_state"], "GA")
        self.assertEqual(american["shipping_zip"], "30076")
