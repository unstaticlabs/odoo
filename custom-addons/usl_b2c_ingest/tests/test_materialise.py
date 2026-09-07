"""Turning a resolved drop into commerce, sales and stock."""

from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import tagged

from . import fixtures
from .test_import_batch import TestImportBatch
from .test_supplier import MODEL, _Supplier


@tagged("post_install", "-at_install", "usl_b2c_ingest")
class TestMaterialise(TestImportBatch):
    """A sale totals what was paid, is taxed by where it went, and ships once."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.france = cls.env.ref("base.fr")
        cls.germany = cls.env.ref("base.de")
        cls.company.account_fiscal_country_id = cls.france
        # The generic chart ships positions that answer for every country. They
        # are exactly what shadows a position built for one, so they are retired
        # here for the same reason a real shop retires them.
        cls.env["account.fiscal.position"].search(
            [("company_id", "=", cls.company.id), ("auto_apply", "=", True)],
        ).active = False
        cls.income = cls._account("Invented revenue", "income")
        cls.carriage_account = cls._account("Invented carriage", "income")
        cls.tax_due = cls._account("Invented VAT due", "liability_current")
        cls.tax_group = cls.env["account.tax.group"].create(
            {"name": "Invented VAT", "company_id": cls.company.id, "country_id": cls.france.id},
        )
        cls.home_tax = cls._tax("Invented 20% FR", 20, cls.france)
        cls.away_tax = cls._tax("Invented 19% DE", 19, cls.france)
        # A destination tax states which tax it replaces; a position states
        # which destination taxes it uses. A position naming none removes the
        # tax entirely, which is what an export outside the Union means.
        cls.home_tax.original_tax_ids = [(6, 0, cls.home_tax.ids)]
        cls.away_tax.original_tax_ids = [(6, 0, cls.home_tax.ids)]
        cls._position("Invented domestic", cls.france, cls.home_tax,
                      sequence=10, is_domestic=True)
        cls._position("Invented Germany", cls.germany, cls.away_tax, sequence=20)
        cls._position("Invented export", False, cls.env["account.tax"], sequence=90)
        cls.carriage = cls.env["product.product"].create(
            {
                "name": "Invented carriage",
                "type": "service",
                "taxes_id": [(6, 0, cls.home_tax.ids)],
                "property_account_income_id": cls.carriage_account.id,
            },
        )
        cls.channels["etsy"].shipping_product_id = cls.carriage
        cls.channels["medusa"].shipping_product_id = cls.carriage

    @classmethod
    def _account(cls, name, account_type):
        return cls.env["account.account"].create(
            {
                "name": name,
                "code": f"INV{abs(hash(name)) % 10000:04d}",
                "account_type": account_type,
                "company_ids": [(6, 0, cls.company.ids)],
            },
        )

    @classmethod
    def _tax(cls, name, amount, country):
        return cls.env["account.tax"].create(
            {
                "name": name,
                "amount": amount,
                "amount_type": "percent",
                "type_tax_use": "sale",
                "country_id": country.id,
                "company_id": cls.company.id,
                "tax_group_id": cls.tax_group.id,
                "price_include_override": "tax_included",
            },
        )

    @classmethod
    def _position(cls, name, country, destinations, *, sequence, is_domestic=False):
        return cls.env["account.fiscal.position"].create(
            {
                "name": name,
                "company_id": cls.company.id,
                "country_id": country.id if country else False,
                "auto_apply": True,
                "sequence": sequence,
                "is_domestic": is_domestic,
                "tax_ids": [(6, 0, destinations.ids)],
            },
        )

    def _product(self, name, *, storable=False):
        return self.env["product.template"].create(
            {
                "name": name,
                "type": "consu",
                "is_storable": storable,
                "list_price": 0.0,
                "taxes_id": [(6, 0, self.home_tax.ids)],
                "property_account_income_id": self.income.id,
            },
        ).product_variant_ids

    def _alias(self, channel, product, **values):
        return self.env["b2c.product.alias"].create(
            {
                "company_id": self.company.id,
                "channel_id": self.channels[channel].id,
                "source_provider": channel,
                "product_id": product.id,
                "mapping_state": "verified",
            }
            | values,
        )

    def _etsy_drop(self, storable=False):
        jersey = self._product("Invented Jersey", storable=storable)
        cap = self._product("Invented Cap", storable=storable)
        self._alias("etsy", jersey, original_sku="FICTION-JERSEY-BLACK-M",
                    original_name="Invented Jersey", original_variation="Color:Black,Size:M")
        self._alias("etsy", cap, original_sku="FICTION-CAP-WHITE",
                    original_name="Invented Cap", original_variation="Color:White")
        batch = self._batch(
            **{
                "etsy-orders.csv": fixtures.etsy_orders(),
                "etsy-items.csv": fixtures.etsy_order_items(),
            },
        )
        batch.action_parse()
        batch.action_resolve()
        return batch, jersey, cap

    def _sale(self, batch, external_order_id):
        return batch.applied_sale_ids.filtered(
            lambda sale: sale.client_order_ref == external_order_id,
        )

    def test_a_sale_totals_exactly_what_the_customer_paid(self):
        batch, _jersey, _cap = self._etsy_drop()
        batch.action_apply()
        self.assertEqual(len(batch.applied_sale_ids), 2)
        # 40.00 of goods and 5.00 of carriage, taxed at the German rate inside it.
        german = self._sale(batch, "9000000001")
        self.assertAlmostEqual(german.amount_total, 45.00, places=2)
        self.assertGreater(german.amount_tax, 0)
        self.assertEqual(german.fiscal_position_id.name, "Invented Germany")
        # 70.00 of goods less 7.00 of coupon and 6.00 of carriage, exported untaxed.
        american = self._sale(batch, "9000000002")
        self.assertAlmostEqual(american.amount_total, 69.00, places=2)
        self.assertEqual(american.amount_tax, 0)

    def test_an_order_level_discount_becomes_a_line_discount(self):
        batch, _jersey, _cap = self._etsy_drop()
        batch.action_apply()
        american = self._sale(batch, "9000000002")
        goods = american.order_line.filtered(lambda line: line.product_id != self.carriage)
        self.assertEqual(goods.mapped("discount"), [10.0])

    def test_applying_twice_adds_nothing(self):
        batch, _jersey, _cap = self._etsy_drop()
        batch.action_apply()
        sales = self.env["sale.order"].search_count([("company_id", "=", self.company.id)])
        orders = self.env["b2c.order"].search_count([("company_id", "=", self.company.id)])
        batch.action_apply()
        self.assertEqual(
            self.env["sale.order"].search_count([("company_id", "=", self.company.id)]), sales,
        )
        self.assertEqual(
            self.env["b2c.order"].search_count([("company_id", "=", self.company.id)]), orders,
        )

    def test_the_canonical_order_and_its_evidence_are_kept(self):
        batch, _jersey, _cap = self._etsy_drop()
        batch.action_apply()
        order = self._sale(batch, "9000000001").usl_b2c_order_id
        self.assertEqual(order.source_provider, "etsy")
        self.assertEqual(order.business_purpose, "sale")
        self.assertEqual(len(order.line_ids), 1)
        sources = order.source_record_ids
        self.assertEqual(len(sources), 1)
        self.assertTrue(sources.evidence_id)
        self.assertEqual(order.country_id, self.germany)

    def test_a_sale_outside_the_union_is_stated_under_the_named_position(self):
        """Untaxed is the answer to more than one question.

        A consumer buying goods outside the Union and a business buying
        services there both bear nothing, for entirely different reasons, and
        no customer record tells the two apart. Naming the position is the only
        way the invoice can say which supply it was.
        """
        consumer_goods = self._position(
            "Invented consumer export", False, self.env["account.tax"], sequence=95,
        )
        consumer_goods.auto_apply = False
        self.company.usl_b2c_export_position_id = consumer_goods
        batch, _jersey, _cap = self._etsy_drop()
        batch.action_apply()
        american = self._sale(batch, "9000000002")
        self.assertEqual(american.fiscal_position_id, consumer_goods)
        self.assertEqual(american.amount_tax, 0)
        # Inside the Union the destination still decides, not this.
        german = self._sale(batch, "9000000001")
        self.assertEqual(german.fiscal_position_id.name, "Invented Germany")

    def test_naming_no_export_position_leaves_the_choice_to_odoo(self):
        self.assertFalse(self.company.usl_b2c_export_position_id)
        batch, _jersey, _cap = self._etsy_drop()
        batch.action_apply()
        american = self._sale(batch, "9000000002")
        self.assertEqual(american.fiscal_position_id.name, "Invented export")
        self.assertEqual(american.amount_tax, 0)

    def test_a_destination_taxed_by_the_wrong_position_is_refused(self):
        batch, _jersey, _cap = self._etsy_drop()
        self.env["account.fiscal.position"].search(
            [("company_id", "=", self.company.id), ("name", "=", "Invented Germany")],
        ).active = False
        with self.assertRaises(UserError) as caught:
            batch.action_apply()
        self.assertIn("Germany", str(caught.exception))

    def test_a_tax_that_would_be_added_to_the_price_is_refused(self):
        batch, _jersey, _cap = self._etsy_drop()
        self.away_tax.price_include_override = "tax_excluded"
        with self.assertRaises(UserError) as caught:
            batch.action_apply()
        self.assertIn("already", str(caught.exception))

    def test_a_channel_charging_carriage_needs_a_product_for_it(self):
        batch, _jersey, _cap = self._etsy_drop()
        self.channels["etsy"].shipping_product_id = False
        with self.assertRaises(UserError) as caught:
            batch.action_apply()
        self.assertIn("carriage", str(caught.exception))

    def test_a_blocking_finding_stops_the_drop(self):
        batch, _jersey, _cap = self._etsy_drop()
        batch._raise_issue("unknown_product", "Something is unresolved")
        with self.assertRaises(UserError) as caught:
            batch.action_apply()
        self.assertIn("Something is unresolved", str(caught.exception))

    def test_stock_falls_when_the_goods_leave(self):
        batch, jersey, _cap = self._etsy_drop(storable=True)
        warehouse = self.env["stock.warehouse"].search(
            [("company_id", "=", self.company.id)], limit=1,
        )
        self.env["stock.quant"].with_context(inventory_mode=True).create(
            {
                "product_id": jersey.id,
                "location_id": warehouse.lot_stock_id.id,
                "inventory_quantity": 5,
            },
        ).action_apply_inventory()
        self.assertEqual(jersey.with_company(self.company).qty_available, 5)
        batch.action_apply()
        picking = self._sale(batch, "9000000001").picking_ids
        self.assertEqual(picking.state, "done")
        self.assertEqual(jersey.with_company(self.company).qty_available, 4)

    def test_the_delivery_is_dated_when_the_goods_actually_left(self):
        batch, _jersey, _cap = self._etsy_drop()
        batch.action_apply()
        picking = self._sale(batch, "9000000001").picking_ids
        self.assertEqual(str(picking.date_done.date()), "2026-03-06")

    def test_the_supplier_cost_reaches_the_sale_line(self):
        batch, _jersey, _cap = self._etsy_drop()
        self.channels["etsy"].printful_store_id = "11111111"
        with patch(f"{MODEL}._printful_client", return_value=_Supplier(fixtures.PRINTFUL_ORDERS)):
            batch.action_fetch_fulfilment()
        batch.action_apply()
        goods = self._sale(batch, "9000000001").order_line.filtered(
            lambda line: line.product_id != self.carriage,
        )
        self.assertAlmostEqual(goods.purchase_price, 16.00, places=2)

    def test_a_medusa_drop_states_its_destination_and_its_carriage(self):
        collar = self._product("Invented Chain")
        lock = self._product("Invented Padlock")
        cap = self._product("Invented Cap")
        self._alias("medusa", collar, original_sku="FICTION-CHAIN-40",
                    original_name="Invented Chain", original_variation="40 cm")
        self._alias("medusa", lock, original_sku="FICTION-LOCK",
                    original_name="Invented Padlock", original_variation="Black")
        self._alias("medusa", cap, original_sku="FICTION-CAP",
                    original_name="Invented Cap", original_variation="White")
        batch = self._batch(
            **{
                "medusa-orders.csv": fixtures.medusa_full_orders(),
                "medusa-items.csv": fixtures.medusa_order_items(),
            },
        )
        batch.action_parse()
        batch.action_resolve()
        self.assertNotIn("destination_unknown", {issue.kind for issue in batch.issue_ids})
        batch.action_apply()
        german = self._sale(batch, "8000000001")
        self.assertAlmostEqual(german.amount_total, 75.00, places=2)
        self.assertEqual(german.fiscal_position_id.name, "Invented Germany")
        american = self._sale(batch, "8000000002")
        self.assertAlmostEqual(american.amount_total, 69.00, places=2)
        self.assertEqual(american.currency_id.name, "GBP")

    def test_an_item_export_alone_cannot_say_where_the_goods_went(self):
        batch = self._batch(**{"medusa-items.csv": fixtures.medusa_order_items()})
        batch.action_parse()
        batch.action_resolve()
        issue = batch.issue_ids.filtered(lambda item: item.kind == "destination_unknown")
        self.assertTrue(issue)
        self.assertIn("orders export", issue.note)
