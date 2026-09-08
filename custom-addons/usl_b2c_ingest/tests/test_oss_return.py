"""What each Member State is owed, quarter by quarter."""

from odoo.exceptions import UserError
from odoo.tests import tagged

from .test_accounting import TestAccounting


@tagged("post_install", "-at_install", "usl_b2c_ingest")
class TestOssReturn(TestAccounting):
    """A return states the taxable amount and the VAT each destination bore."""

    def _return(self, year=2026, quarter="1"):
        return self.env["b2c.oss.return"].create(
            {"company_id": self.company.id, "year": year, "quarter": quarter},
        )

    def _invoiced(self):
        batch = self._applied()
        batch.action_park_destination_vat()
        batch.action_invoice()
        return batch

    def test_a_quarter_states_what_each_country_bore(self):
        self._invoiced()
        record = self._return()
        record.action_compute()
        self.assertEqual(len(record.line_ids), 1)
        line = record.line_ids
        self.assertEqual(line.country_id, self.germany)
        self.assertEqual(line.rate, 19.0)
        # 45.00 paid, of which 19 per cent is the German VAT inside it.
        self.assertAlmostEqual(line.base_amount, 37.82, places=2)
        self.assertAlmostEqual(line.vat_amount, 7.18, places=2)
        self.assertAlmostEqual(record.base_amount + record.vat_amount, 45.00, places=2)

    def test_a_sale_outside_the_union_is_not_in_the_return(self):
        self._invoiced()
        record = self._return()
        record.action_compute()
        self.assertNotIn(
            self.env.ref("base.us"), record.line_ids.mapped("country_id"),
        )

    def test_another_quarter_holds_none_of_it(self):
        self._invoiced()
        record = self._return(quarter="4")
        record.action_compute()
        self.assertFalse(record.line_ids)

    def test_computing_twice_states_the_same_thing(self):
        self._invoiced()
        record = self._return()
        record.action_compute()
        first = (len(record.line_ids), record.base_amount, record.vat_amount)
        record.action_compute()
        self.assertEqual(
            (len(record.line_ids), record.base_amount, record.vat_amount), first,
        )

    def test_a_return_says_whether_the_shop_can_yet_file_it(self):
        record = self._return()
        self.assertFalse(record.registered)
        self.company.usl_b2c_oss_registered = True
        self.assertTrue(record.registered)

    def test_an_empty_return_cannot_be_called_ready(self):
        record = self._return()
        record.action_compute()
        with self.assertRaises(UserError):
            record.action_mark_ready()

    def test_a_computed_return_is_filed_through_ready(self):
        self._invoiced()
        record = self._return()
        record.action_compute()
        record.action_mark_ready()
        self.assertEqual(record.state, "ready")
        record.action_mark_filed()
        self.assertEqual(record.state, "filed")

    def test_one_return_covers_one_quarter(self):
        self._return()
        with self.assertRaises(Exception):
            self._return()
            self.env.flush_all()
