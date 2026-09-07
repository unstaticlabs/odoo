"""The value rules a rerun is proved with must match what Odoo stored."""

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from odoo.addons.usl_b2c_restore.models.native_history.comparison import (
    assert_values,
    value_differs,
)


@tagged("post_install", "-at_install")
class TestNativeHistoryComparison(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env["res.partner"].create({"name": "Comparison probe"})
        cls.order = cls.env["sale.order"].create({"partner_id": cls.partner.id})
        cls.line = cls.env["sale.order.line"].create(
            {
                "order_id": cls.order.id,
                "name": "Probe",
                "product_uom_qty": 1,
                "price_unit": 10,
                "discount": 8.333333333333334,
            },
        )

    def test_a_float_compares_at_the_precision_it_is_stored_with(self):
        """A discount Odoo rounded to two places is not drift from its source."""
        digits = self.env["decimal.precision"].precision_get("Discount")
        self.assertEqual(digits, 2, "This test describes a two-decimal discount.")
        self.assertEqual(self.line.discount, 8.33)
        self.assertFalse(
            value_differs(self.line, "discount", 8.333333333333334),
            "An exact source decimal must match the value Odoo could store.",
        )
        self.assertTrue(
            value_differs(self.line, "discount", 8.34),
            "A difference the column can hold is real drift.",
        )

    def test_money_compares_at_the_currency_rounding(self):
        currency = self.order.currency_id
        self.assertFalse(value_differs(self.line, "price_unit", 10))
        self.assertTrue(
            value_differs(self.order, "amount_total", self.order.amount_total + 1),
        )
        self.assertFalse(
            value_differs(
                self.order,
                "amount_total",
                self.order.amount_total + currency.rounding / 10,
            ),
        )

    def test_a_relation_matches_as_a_record_or_as_a_stored_id(self):
        self.assertFalse(value_differs(self.order, "partner_id", self.partner))
        self.assertFalse(value_differs(self.order, "partner_id", self.partner.id))
        self.assertTrue(value_differs(self.order, "partner_id", self.env["res.partner"]))
        self.assertTrue(value_differs(self.order, "partner_id", False))

    def test_an_empty_relation_matches_an_empty_expectation(self):
        self.order.fiscal_position_id = False
        self.assertFalse(value_differs(self.line, "tax_ids", self.env["account.tax"]))
        self.assertFalse(value_differs(self.order, "fiscal_position_id", False))
        self.assertFalse(
            value_differs(
                self.order, "fiscal_position_id", self.env["account.fiscal.position"],
            ),
        )

    def test_drift_is_reported_with_the_field_that_moved(self):
        with self.assertRaises(UserError) as error:
            assert_values(
                self.order,
                {"partner_id": self.partner, "name": "not the order name"},
                "Probe order",
            )
        message = str(error.exception)
        self.assertIn("Probe order", message)
        self.assertIn("name", message)
        self.assertNotIn("partner_id", message)
