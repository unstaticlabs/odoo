from datetime import datetime
from pathlib import Path
from runpy import run_path
from types import SimpleNamespace

from psycopg2 import IntegrityError

from odoo import Command
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.usl_b2c.models.constants import (
    HISTORICAL_B2C_COMMUNICATION_PARAMETER,
    HISTORICAL_B2C_MATERIALIZATION_CONTEXT,
)
from odoo.addons.usl_b2c.models.native_history import MATERIALIZATION_TOKEN


@tagged("post_install", "-at_install")
class TestB2cFoundation(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.other_company = cls.env["res.company"].create(
            {"name": "B2C isolation company"},
        )
        cls.plan = cls.env["account.analytic.plan"].search(
            [("name", "=", "Channel")],
            limit=1,
        )
        if not cls.plan:
            cls.plan = cls.env["account.analytic.plan"].create({"name": "Channel"})
        cls.analytic = cls.env["account.analytic.account"].create(
            {
                "name": "Direct",
                "plan_id": cls.plan.id,
                "company_id": cls.company.id,
            },
        )
        cls.channel = cls.env["b2c.channel"].create(
            {
                "name": "B2C test direct",
                "code": "test_direct",
                "company_id": cls.company.id,
                "analytic_account_id": cls.analytic.id,
            },
        )
        cls.reader = new_test_user(
            cls.env,
            login="b2c_reader",
            groups="usl_b2c.group_b2c_reader",
        )
        cls.operator = new_test_user(
            cls.env,
            login="b2c_operator",
            groups="usl_b2c.group_b2c_operator",
        )
        cls.manager = new_test_user(
            cls.env,
            login="b2c_manager",
            groups="usl_b2c.group_b2c_manager",
        )
        cls.irreversible_manager = cls.manager
        if cls.env.ref(
            "usl_access_control.group_irreversible_actions",
            raise_if_not_found=False,
        ):
            cls.irreversible_manager = new_test_user(
                cls.env,
                login="b2c_irreversible_manager",
                groups=(
                    "usl_b2c.group_b2c_manager,"
                    "usl_access_control.group_irreversible_actions"
                ),
            )
        cls.pack_unpacker = new_test_user(
            cls.env,
            login="b2c_pack_unpacker",
            groups="usl_b2c.group_b2c_pack_unpacker",
        )
        cls.unauthorized = new_test_user(
            cls.env,
            login="b2c_unauthorized",
            groups="base.group_user",
        )
        cls.evidence = (
            cls.env["b2c.provider.evidence"]
            .sudo()
            .with_context(b2c_evidence_import=True)
            .create(
                {
                    "evidence_key": "test:evidence:1",
                    "company_id": cls.company.id,
                    "source_provider": "manual",
                    "source_name": "synthetic-fixture.csv",
                    "source_checksum": "a" * 64,
                    "schema_digest": "b" * 64,
                    "payload_digest": "c" * 64,
                    "payload_json": {"fixture": True},
                    "contains_pii": False,
                    "occurred_at": datetime(2026, 8, 4, 10, 0),
                },
            )
        )

    def _order_values(self, suffix="1"):
        currency = self.company.currency_id
        return {
            "name": f"B2C-{suffix}",
            "canonical_key": f"manual:order:{suffix}",
            "company_id": self.company.id,
            "channel_id": self.channel.id,
            "source_provider": "manual",
            "origin": "manual",
            "external_order_id": suffix,
            "state": "fulfilled",
            "order_date": datetime(2026, 8, 4, 10, 0),
            "currency_id": currency.id,
            "revenue_amount": 120,
            "revenue_company_amount": 120,
            "total_amount": 120,
            "total_company_amount": 120,
            "net_amount": 120,
            "net_company_amount": 120,
            "conversion_state": "not_needed",
            "amount_completeness": "complete",
            "mapping_state": "pending",
            "review_state": "pending",
            "fulfilment_mode": "own_stock",
        }

    def test_delivery_does_not_activate_cash_on_delivery(self):
        provider = self.env.ref("delivery.payment_provider_cod")
        self.assertEqual(provider.state, "disabled")

    def test_native_inventory_foundations_are_enabled_without_role_escalation(self):
        internal_user = self.env.ref("base.group_user")
        for xmlid in (
            "product.group_product_variant",
            "stock.group_stock_multi_locations",
            "stock.group_production_lot",
            "uom.group_uom",
        ):
            self.assertIn(self.env.ref(xmlid), internal_user.implied_ids)
        self.assertTrue(self.env["stock.warehouse"].search([]).mapped("int_type_id.active"))
        self.assertEqual(
            self.env["ir.module.module"].search(
                [("name", "=", "stock_landed_costs")],
                limit=1,
            ).state,
            "installed",
        )
        self.assertFalse(self.unauthorized.has_group("stock.group_stock_manager"))
        unpacker = self.env.ref("usl_b2c.group_b2c_pack_unpacker")
        self.assertIn(self.env.ref("mrp.group_mrp_user"), unpacker.implied_ids)
        self.assertNotIn(unpacker, self.operator.group_ids)

    def test_source_name_variation_aliases_are_distinct_without_a_sku(self):
        base_values = {
            "company_id": self.company.id,
            "channel_id": self.channel.id,
            "source_provider": "medusa",
            "original_name": "Ankle Chains",
        }
        small = self.env["b2c.product.alias"].create(
            {
                **base_values,
                "original_variation": "S / 3 mm / One chain",
            },
        )
        medium = self.env["b2c.product.alias"].create(
            {
                **base_values,
                "original_variation": "M / 3 mm / One chain",
            },
        )
        self.assertNotEqual(small.alias_key, medium.alias_key)
        with self.assertRaises(IntegrityError):
            self.env["b2c.product.alias"].create(
                {
                    **base_values,
                    "original_variation": "S / 3 mm / One chain",
                },
            )
            self.env.flush_all()

    def test_listing_variations_without_skus_keep_distinct_aliases(self):
        base_values = {
            "company_id": self.company.id,
            "channel_id": self.channel.id,
            "source_provider": "etsy",
            "external_listing_id": "listing-42",
            "original_name": "POD cap",
        }
        red = self.env["b2c.product.alias"].create(
            {**base_values, "original_variation": "Color:Red"},
        )
        blue = self.env["b2c.product.alias"].create(
            {**base_values, "original_variation": "Color:Blue"},
        )
        self.assertNotEqual(red.alias_key, blue.alias_key)

    def test_generic_provider_sku_keeps_exact_variations_distinct(self):
        base_values = {
            "company_id": self.company.id,
            "channel_id": self.channel.id,
            "source_provider": "etsy",
            "external_listing_id": "1838821663",
            "original_sku": "67544159BCEB6_10780",
            "source_sku_is_unique": False,
            "original_name": "good boys obey – hoodie summer (2025.06) – limited edition",
        }
        black_small = self.env["b2c.product.alias"].create(
            {**base_values, "original_variation": "Color:Black,Men's chest size:S"},
        )
        maroon_medium = self.env["b2c.product.alias"].create(
            {**base_values, "original_variation": "Color:Maroon,Men's chest size:M"},
        )
        self.assertEqual(black_small.original_sku, "67544159BCEB6_10780")
        self.assertEqual(maroon_medium.original_sku, "67544159BCEB6_10780")
        self.assertNotEqual(black_small.alias_key, maroon_medium.alias_key)

    def test_reconfigured_etsy_listing_has_two_product_generations(self):
        migration = run_path(
            Path(__file__).parents[1]
            / "migrations"
            / "saas~19.3.1.1.0"
            / "catalog_normalization.py",
        )
        original = SimpleNamespace(
            source_provider="etsy",
            external_listing_id="1838821663",
            original_name="Good Boys Obey Hoodie by SBFH",
        )
        summer = SimpleNamespace(
            source_provider="etsy",
            external_listing_id="1838821663",
            original_name="good boys obey – hoodie summer (2025.06) – limited edition",
        )
        self.assertEqual(migration["family_key"](original), "1838821663::original")
        self.assertEqual(
            migration["family_key"](summer),
            "1838821663::summer-2025-06",
        )
        self.assertFalse(
            migration["source_sku_is_unique"](
                "etsy",
                "1838821663::summer-2025-06",
                "67544159BCEB6_10780",
            ),
        )
        self.assertTrue(
            migration["source_sku_is_unique"](
                "etsy",
                "1838821663::original",
                "67544159BCEB6_10780",
            ),
        )

    def test_supplier_pack_opens_exact_native_unbuild_recipe(self):
        unit = self.env["product.product"].create(
            {
                "name": "Test saleable lock",
                "is_storable": True,
                "purchase_ok": False,
            },
        )
        unit.product_tmpl_id.b2c_inventory_role = "saleable_unit"
        pack = self.env["product.product"].create(
            {
                "name": "Test supplier two-pack",
                "is_storable": True,
                "sale_ok": False,
                "purchase_ok": True,
            },
        )
        pack.product_tmpl_id.b2c_inventory_role = "supplier_pack"
        bom = self.env["mrp.bom"].create(
            {
                "product_tmpl_id": pack.product_tmpl_id.id,
                "product_id": pack.id,
                "product_qty": 1,
                "uom_id": pack.uom_id.id,
                "type": "normal",
                "bom_line_ids": [
                    Command.create(
                        {
                            "product_id": unit.id,
                            "product_qty": 2,
                            "uom_id": unit.uom_id.id,
                        },
                    ),
                ],
            },
        )
        action = pack.with_user(self.pack_unpacker).action_usl_unpack_supplier_pack()
        self.assertEqual(action["res_model"], "mrp.unbuild")
        self.assertEqual(action["context"]["default_product_id"], pack.id)
        self.assertEqual(action["context"]["default_bom_id"], bom.id)
        self.assertEqual(bom.bom_line_ids.product_qty, 2)
        template_action = pack.product_tmpl_id.with_user(
            self.pack_unpacker,
        ).action_usl_unpack_supplier_pack()
        self.assertEqual(template_action["context"], action["context"])
        with self.assertRaises(AccessError):
            pack.with_user(self.unauthorized).action_usl_unpack_supplier_pack()

        location = self.env["stock.warehouse"].search(
            [("company_id", "=", self.company.id)],
            limit=1,
        ).lot_stock_id
        self.env["stock.quant"]._update_available_quantity(pack, location, 1)
        unbuild = self.env["mrp.unbuild"].create(
            {
                "company_id": self.company.id,
                "product_id": pack.id,
                "product_qty": 1,
                "uom_id": pack.uom_id.id,
                "bom_id": bom.id,
                "location_id": location.id,
                "location_dest_id": location.id,
            },
        )
        unbuild.action_validate()
        self.assertEqual(unbuild.state, "done")
        self.assertEqual(
            self.env["stock.quant"]._get_available_quantity(pack, location),
            0,
        )
        self.assertEqual(
            self.env["stock.quant"]._get_available_quantity(unit, location),
            2,
        )

    def test_zero_stock_allocation_placeholder_is_archived(self):
        placeholder = self.env["product.product"].create(
            {
                "name": "Master locks awaiting colour allocation",
                "default_code": "PADLOCK_MASTER_9120EUR_ASSORTED_UNALLOCATED",
                "is_storable": True,
                "sale_ok": False,
                "purchase_ok": False,
            },
        )
        pack = self.env["product.product"].create(
            {
                "name": "Master assorted supplier pack",
                "default_code": "GBC-ML-9120-QCOLNOP",
                "is_storable": True,
                "sale_ok": False,
                "purchase_ok": True,
            },
        )
        bom = self.env["mrp.bom"].create(
            {
                "product_tmpl_id": pack.product_tmpl_id.id,
                "product_id": pack.id,
                "product_qty": 1,
                "uom_id": pack.uom_id.id,
                "type": "normal",
                "bom_line_ids": [
                    Command.create(
                        {
                            "product_id": placeholder.id,
                            "product_qty": 4,
                            "uom_id": placeholder.uom_id.id,
                        },
                    ),
                ],
            },
        )
        migration = run_path(
            Path(__file__).parents[1]
            / "migrations"
            / "saas~19.3.1.2.2"
            / "post-archive-empty-allocation-product.py",
        )

        migration["migrate"](self.env.cr, "saas~19.3.1.2.1")
        self.env.invalidate_all()

        self.assertFalse(placeholder.product_tmpl_id.active)
        self.assertFalse(bom.active)
        self.assertTrue(pack.product_tmpl_id.active)
        self.assertEqual(
            placeholder.product_tmpl_id.b2c_catalog_classification,
            "legacy",
        )

    def test_catalog_variation_parser_preserves_real_attributes(self):
        migration = run_path(
            Path(__file__).parents[1]
            / "migrations"
            / "saas~19.3.1.1.0"
            / "catalog_normalization.py",
        )
        self.assertEqual(
            migration["parse_etsy_variation"](
                "Color:French Navy,Men's chest size:L US letter",
            ),
            (("colour", "French Navy"), ("size", "L US letter")),
        )
        self.assertEqual(
            migration["parse_medusa_variation"](
                "ankle chains",
                "M (26cm) / 4mm (14x21mm links) / Two Chains With Padlocks",
            ),
            (
                ("size", "M (26cm)"),
                ("diameter", "4mm (14x21mm links)"),
                ("configuration", "Two Chains With Padlocks"),
            ),
        )
        self.assertEqual(
            migration["parse_medusa_variation"](
                "master padlock 20mm",
                "Blue - One",
            ),
            (("colour", "Blue"), ("package", "One")),
        )
        self.assertEqual(
            migration["MASTER_UNIT_CODES"],
            {
                "PADLOCK_MASTER_9120EUR_BLACK": "Black",
                "PADLOCK_MASTER_9120EUR_BLUE": "Blue",
                "PADLOCK_MASTER_9120EUR_GREEN": "Green",
                "PADLOCK_MASTER_9120EUR_PINK": "Pink",
                "PADLOCK_MASTER_9120EUR_PURPLE": "Purple",
            },
        )
        self.assertEqual(
            migration["LEGACY_UNALLOCATED_CODES"],
            {
                "PADLOCK_MASTER_9120EUR_ASSORTED_UNALLOCATED",
                "PADLOCK_QD40_UNALLOCATED_2026-05",
            },
        )
        self.assertNotIn("GBC-ML-9120-QCOLNOP", migration["MASTER_PACK_CONTENTS"])

    def test_catalog_variant_matrix_extends_but_never_rewrites(self):
        migration = run_path(
            Path(__file__).parents[1]
            / "migrations"
            / "saas~19.3.1.1.0"
            / "catalog_normalization.py",
        )
        normalizer = migration["CatalogNormalizer"](self.env, "apply")
        colour = normalizer._attribute("colour")
        black = normalizer._attribute_value(colour, "Black")
        blue = normalizer._attribute_value(colour, "Blue")
        template = self.env["product.template"].create(
            {
                "name": "Synthetic expandable variant family",
                "attribute_line_ids": [
                    Command.create(
                        {
                            "attribute_id": colour.id,
                            "value_ids": [Command.set([black.id, blue.id])],
                        },
                    ),
                ],
            },
        )
        variants = normalizer._ensure_variant_matrix(
            template,
            [(('colour', value),) for value in ("Black", "Blue", "Purple")],
        )
        self.assertEqual(len(variants), 3)
        self.assertEqual(template.product_variant_count, 3)
        with self.assertRaises(migration["CatalogNormalizationError"]):
            normalizer._ensure_variant_matrix(template, [(('colour', "Black"),)])

    def test_synthetic_lot_transfer_and_draft_landed_cost(self):
        category = self.env.ref("product.product_category_goods").copy(
            {
                "name": "Synthetic average-cost category",
                "property_cost_method": "average",
                "property_valuation": "real_time",
            },
        )
        warehouse = self.env["stock.warehouse"].search(
            [("company_id", "=", self.company.id)],
            limit=1,
        )
        supplier_location = self.env.ref("stock.stock_location_suppliers")
        expense_journal = self.env["account.journal"].search(
            [("company_id", "=", self.company.id), ("type", "=", "purchase")],
            limit=1,
        )
        product = self.env["product.product"].create(
            {
                "name": "Synthetic inventory-foundations product",
                "is_storable": True,
                "tracking": "lot",
                "categ_id": category.id,
                "standard_price": 10,
                "weight": 2,
                "volume": 0.5,
            },
        )
        lot = self.env["stock.lot"].create(
            {"name": "SYNTHETIC-LOT-001", "product_id": product.id},
        )
        receipt = self.env["stock.picking"].create(
            {
                "picking_type_id": warehouse.in_type_id.id,
                "location_id": supplier_location.id,
                "location_dest_id": warehouse.lot_stock_id.id,
                "move_ids": [
                    Command.create(
                        {
                            "product_id": product.id,
                            "product_uom_qty": 5,
                            "uom_id": product.uom_id.id,
                            "location_id": supplier_location.id,
                            "location_dest_id": warehouse.lot_stock_id.id,
                        },
                    ),
                ],
            },
        )
        receipt.action_confirm()
        receipt.move_ids.quantity = 5
        receipt.move_ids.move_line_ids.lot_id = lot
        receipt.button_validate()

        secondary = self.env["stock.location"].create(
            {
                "name": "Synthetic secondary storage",
                "location_id": warehouse.view_location_id.id,
                "usage": "internal",
            },
        )
        transfer = self.env["stock.picking"].create(
            {
                "picking_type_id": warehouse.int_type_id.id,
                "location_id": warehouse.lot_stock_id.id,
                "location_dest_id": secondary.id,
                "move_ids": [
                    Command.create(
                        {
                            "product_id": product.id,
                            "product_uom_qty": 2,
                            "uom_id": product.uom_id.id,
                            "location_id": warehouse.lot_stock_id.id,
                            "location_dest_id": secondary.id,
                        },
                    ),
                ],
            },
        )
        transfer.action_confirm()
        transfer.action_assign()
        transfer.move_ids.quantity = 2
        transfer.move_ids.move_line_ids.lot_id = lot
        transfer.button_validate()

        freight = self.env["product.product"].create(
            {
                "name": "Synthetic freight",
                "type": "service",
                "landed_cost_ok": True,
                "categ_id": category.id,
            },
        )
        landed_cost = self.env["stock.landed.cost"].create(
            {
                "picking_ids": [Command.set(receipt.ids)],
                "account_journal_id": expense_journal.id,
                "cost_lines": [
                    Command.create(
                        {
                            "name": "Synthetic freight",
                            "product_id": freight.id,
                            "price_unit": 25,
                            "split_method": "by_quantity",
                        },
                    ),
                ],
            },
        )
        landed_cost.compute_landed_cost()

        self.assertEqual(landed_cost.state, "draft")
        self.assertEqual(sum(landed_cost.valuation_adjustment_lines.mapped("additional_landed_cost")), 25)
        self.assertFalse(landed_cost.account_move_id)
        self.assertEqual(
            self.env["stock.quant"]._get_available_quantity(product, secondary, lot_id=lot),
            2,
        )

    def test_coverage_fields_keep_zero_to_one_hundred_display_contract(self):
        self.assertEqual(
            self.env["b2c.order"]._fields[
                "line_revenue_coverage_percent"
            ].string,
            "Line Revenue Coverage (%)",
        )
        self.assertEqual(
            self.env["b2c.accounting.session"]._fields[
                "accounting_link_coverage_percent"
            ].string,
            "Accounting Disposition Coverage (%)",
        )
        for xmlid in (
            "usl_b2c.view_b2c_order_list",
            "usl_b2c.view_b2c_order_form",
            "usl_b2c.view_b2c_accounting_session_list",
            "usl_b2c.view_b2c_accounting_session_form",
        ):
            self.assertNotIn('widget="percentage"', self.env.ref(xmlid).arch_db)

    def test_acl_and_company_isolation(self):
        order = self.env["b2c.order"].create(self._order_values("acl"))
        self.assertEqual(order.with_user(self.reader).name, "B2C-acl")
        with self.assertRaises(AccessError):
            order.with_user(self.reader).write({"notes": "forbidden"})
        order.with_user(self.operator).write({"notes": "reviewed by operator"})
        with self.assertRaises(AccessError):
            self.env["b2c.order"].with_user(self.unauthorized).search([])

        other_analytic = self.env["account.analytic.account"].create(
            {
                "name": "Other channel",
                "plan_id": self.plan.id,
                "company_id": self.other_company.id,
            },
        )
        other_channel = self.env["b2c.channel"].create(
            {
                "name": "Other",
                "code": "other",
                "company_id": self.other_company.id,
                "analytic_account_id": other_analytic.id,
            },
        )
        other_values = self._order_values("other-company")
        other_values.update(
            {"company_id": self.other_company.id, "channel_id": other_channel.id},
        )
        other_order = self.env["b2c.order"].create(other_values)
        visible = (
            self.env["b2c.order"]
            .with_user(self.reader)
            .with_context(allowed_company_ids=[self.company.id])
            .search([("id", "in", [order.id, other_order.id])])
        )
        self.assertEqual(visible, order)

        other_evidence = (
            self.env["b2c.provider.evidence"]
            .sudo()
            .with_context(b2c_evidence_import=True)
            .create(
                {
                    "evidence_key": "test:evidence:other-company",
                    "company_id": self.other_company.id,
                    "source_provider": "manual",
                    "source_name": "other-company.csv",
                    "source_checksum": "1" * 64,
                    "schema_digest": "2" * 64,
                    "payload_digest": "3" * 64,
                    "payload_json": {"fixture": True},
                    "contains_pii": False,
                },
            )
        )
        with self.assertRaises(UserError):
            self.env["b2c.product.alias"].create(
                {
                    "company_id": self.company.id,
                    "channel_id": self.channel.id,
                    "source_provider": "manual",
                    "original_sku": "CROSS-COMPANY",
                    "evidence_id": other_evidence.id,
                },
            )

    def test_historical_sales_are_quiet_locked_and_non_invoiceable(self):
        source = self.env["b2c.order"].create(self._order_values("native-history"))
        partner = self.env["res.partner"].create(
            {"name": "Historical recipient", "email": "history@example.invalid"},
        )
        product = self.env["product.product"].create(
            {"name": "Historical product", "list_price": 12},
        )
        materialization_context = {
            HISTORICAL_B2C_MATERIALIZATION_CONTEXT: MATERIALIZATION_TOKEN,
        }
        sale = self.env["sale.order"].sudo().with_context(
            **materialization_context
        ).create(
            {
                "partner_id": partner.id,
                "company_id": self.company.id,
                "usl_b2c_order_id": source.id,
                "usl_historical_b2c": True,
                "usl_historical_b2c_completed": True,
            },
        )
        line = self.env["sale.order.line"].sudo().with_context(
            **materialization_context
        ).create(
            {
                "order_id": sale.id,
                "product_id": product.id,
                "product_uom_qty": 1,
                "price_unit": 12,
            },
        )
        parameter = self.env["ir.config_parameter"].sudo()
        parameter.set_bool(HISTORICAL_B2C_COMMUNICATION_PARAMETER, False)

        with self.assertRaisesRegex(UserError, "Communication from historical"):
            sale.with_user(self.manager).action_quotation_send()
        with self.assertRaisesRegex(UserError, "cannot create invoices"):
            sale.with_user(self.manager)._create_invoices()
        with self.assertRaisesRegex(UserError, "cannot create payment links"):
            sale.with_user(self.manager)._get_default_payment_link_values()
        with self.assertRaisesRegex(UserError, "locked"):
            line.with_user(self.manager).write({"price_unit": 13})
        with self.assertRaisesRegex(UserError, "order lines are locked"):
            self.env["sale.order.line"].sudo().create(
                {
                    "order_id": sale.id,
                    "product_id": product.id,
                    "product_uom_qty": 1,
                    "price_unit": 12,
                },
            )
        with self.assertRaisesRegex(UserError, "locked"):
            sale.with_user(self.manager).write({"pricelist_id": False})
        with self.assertRaisesRegex(UserError, "locked"):
            sale.with_user(self.manager).with_context(
                **materialization_context
            ).write({"pricelist_id": False})
        with self.assertRaisesRegex(UserError, "locked"):
            sale.sudo().with_context(
                **{HISTORICAL_B2C_MATERIALIZATION_CONTEXT: True},
            ).write({"pricelist_id": False})
        with self.assertRaisesRegex(AccessError, "provenance is immutable"):
            sale.with_user(self.manager).write({"usl_source_total": 99})
        with self.assertRaisesRegex(AccessError, "provenance is immutable"):
            source.with_user(self.manager).write({"sale_order_id": sale.id})

        provider = self.env.ref("delivery.payment_provider_cod")
        payment_method = self.env.ref("payment.payment_method_unknown")
        transaction_values = {
            "provider_id": provider.id,
            "payment_method_id": payment_method.id,
            "reference": "historical-payment-attempt",
            "amount": sale.amount_total,
            "currency_id": sale.currency_id.id,
            "partner_id": sale.partner_invoice_id.id,
        }
        with self.assertRaisesRegex(UserError, "cannot create payment transactions"):
            self.env["payment.transaction"].sudo().create(
                {
                    **transaction_values,
                    # The portal payment controller uses SET, while ordinary
                    # server-side code commonly uses LINK. Both must resolve
                    # through the same historical-order guard.
                    "sale_order_ids": [Command.set([sale.id])],
                },
            )
        transaction = self.env["payment.transaction"].sudo().create(
            transaction_values,
        )
        with self.assertRaisesRegex(UserError, "cannot be linked"):
            transaction.write({"sale_order_ids": [Command.link(sale.id)]})
        with self.assertRaisesRegex(UserError, "cannot be linked"):
            sale.sudo().write({"transaction_ids": [Command.link(transaction.id)]})
        self.assertFalse(transaction.sale_order_ids)

        mail_count = self.env["mail.mail"].sudo().search_count([])
        sale._send_order_notification_mail(self.env.ref("sale.email_template_edi_sale"))
        self.assertEqual(self.env["mail.mail"].sudo().search_count([]), mail_count)
        note = sale.with_user(self.manager).message_post(
            body="Internal historical review",
            subtype_xmlid="mail.mt_note",
        )
        self.assertEqual(note.model, "sale.order")

        sale.sudo().message_subscribe(
            partner_ids=[partner.id, self.operator.partner_id.id],
        )
        comment = sale.with_user(self.manager).message_post(
            body="Internal update without explicit recipients",
            subtype_xmlid="mail.mt_comment",
        )
        recipient_ids = {
            recipient["id"]
            for recipient in sale._notify_get_recipients(comment)
            if recipient["id"]
        }
        self.assertIn(self.operator.partner_id.id, recipient_ids)
        self.assertNotIn(partner.id, recipient_ids)
        with self.assertRaisesRegex(UserError, "Communication from historical"):
            sale.with_user(self.manager).message_post(
                body="Direct external attempt",
                outgoing_email_to="external@example.invalid",
                subtype_xmlid="mail.mt_comment",
            )

    def test_historical_purchase_is_locked_and_never_contacts_supplier(self):
        supplier = self.env["res.partner"].create(
            {"name": "Historical supplier", "email": "supplier@example.invalid"},
        )
        product = self.env["product.product"].create(
            {"name": "Historical purchase product", "purchase_ok": True},
        )
        context = {
            HISTORICAL_B2C_MATERIALIZATION_CONTEXT: MATERIALIZATION_TOKEN,
        }
        purchase = self.env["purchase.order"].sudo().with_context(**context).create(
            {
                "partner_id": supplier.id,
                "company_id": self.company.id,
                "usl_historical_b2c": True,
                "usl_b2c_source_key": "test:historical-purchase",
            },
        )
        line = self.env["purchase.order.line"].sudo().with_context(**context).create(
            {
                "order_id": purchase.id,
                "product_id": product.id,
                "product_qty": 2,
                "price_unit": 5,
                "date_planned": datetime(2026, 8, 4, 10, 0),
            },
        )

        with self.assertRaisesRegex(UserError, "purchase orders are locked"):
            purchase.with_user(self.manager).with_context({}).write(
                {"partner_ref": "changed"},
            )
        with self.assertRaisesRegex(UserError, "order lines are locked"):
            line.with_user(self.manager).with_context({}).write({"price_unit": 6})
        with self.assertRaisesRegex(UserError, "order lines are locked"):
            self.env["purchase.order.line"].sudo().create(
                {
                    "order_id": purchase.id,
                    "product_id": product.id,
                    "product_qty": 1,
                    "price_unit": 5,
                    "date_planned": datetime(2026, 8, 4, 10, 0),
                },
            )
        with self.assertRaisesRegex(UserError, "purchase orders are locked"):
            purchase.with_user(self.manager).with_context(**context).write(
                {"partner_ref": "spoofed"},
            )
        with self.assertRaisesRegex(UserError, "cannot send supplier emails"):
            purchase.with_user(self.manager).with_context({}).action_rfq_send()
        with self.assertRaisesRegex(UserError, "cannot contact suppliers"):
            purchase.with_user(self.manager).with_context({}).message_post(
                body="Direct supplier attempt",
                outgoing_email_to=supplier.email,
                subtype_xmlid="mail.mt_comment",
            )
        with self.assertRaisesRegex(UserError, "cannot be deleted"):
            purchase.with_user(self.manager).with_context({}).unlink()

        purchase.sudo().message_subscribe(
            partner_ids=[supplier.id, self.operator.partner_id.id],
        )
        comment = purchase.with_user(self.manager).message_post(
            body="Internal acquisition review",
            subtype_xmlid="mail.mt_comment",
        )
        recipient_ids = {
            recipient["id"]
            for recipient in purchase._notify_get_recipients(comment)
            if recipient["id"]
        }
        self.assertIn(self.operator.partner_id.id, recipient_ids)
        self.assertNotIn(supplier.id, recipient_ids)

    def test_historical_inventory_records_resist_structural_edits_and_deletion(self):
        context = {
            HISTORICAL_B2C_MATERIALIZATION_CONTEXT: MATERIALIZATION_TOKEN,
        }
        warehouse = self.env["stock.warehouse"].search(
            [("company_id", "=", self.company.id)],
            limit=1,
        )
        supplier_location = self.env.ref("stock.stock_location_suppliers")
        product = self.env["product.product"].create(
            {
                "name": "Historical inventory guard product",
                "is_storable": True,
            },
        )
        picking = self.env["stock.picking"].sudo().with_context(**context).create(
            {
                "picking_type_id": warehouse.in_type_id.id,
                "location_id": supplier_location.id,
                "location_dest_id": warehouse.lot_stock_id.id,
                "usl_historical_b2c": True,
                "usl_b2c_source_key": "test:historical-receipt",
                "move_ids": [
                    Command.create(
                        {
                            "product_id": product.id,
                            "product_uom_qty": 1,
                            "uom_id": product.uom_id.id,
                            "location_id": supplier_location.id,
                            "location_dest_id": warehouse.lot_stock_id.id,
                        },
                    ),
                ],
            },
        )
        picking.action_confirm()
        picking.move_ids.quantity = 1
        picking.move_ids.picked = True
        picking.button_validate()
        picking = picking.sudo().with_context({})
        move = picking.move_ids.sudo().with_context({})

        with self.assertRaisesRegex(UserError, "stock operations are locked"):
            picking.write({"scheduled_date": datetime(2026, 8, 5, 10, 0)})
        with self.assertRaisesRegex(UserError, "stock moves are locked"):
            move.write({"quantity": 2})
        move_line = move.move_line_ids
        self.assertTrue(move_line)
        with self.assertRaisesRegex(UserError, "stock move lines are locked"):
            move_line.write({"quantity": 2})
        with self.assertRaisesRegex(UserError, "stock moves are locked"):
            self.env["stock.move"].sudo().create(
                {
                    "picking_id": picking.id,
                    "product_id": product.id,
                    "product_uom_qty": 1,
                    "uom_id": product.uom_id.id,
                    "location_id": supplier_location.id,
                    "location_dest_id": warehouse.lot_stock_id.id,
                },
            )
        with self.assertRaisesRegex(UserError, "stock move lines are locked"):
            self.env["stock.move.line"].sudo().create(
                {
                    "move_id": move.id,
                    "product_id": product.id,
                    "quantity": 1,
                    "uom_id": product.uom_id.id,
                    "location_id": supplier_location.id,
                    "location_dest_id": warehouse.lot_stock_id.id,
                },
            )
        with self.assertRaisesRegex(UserError, "move lines cannot be deleted"):
            move_line.unlink()
        with self.assertRaisesRegex(UserError, "stock moves cannot be deleted"):
            move.unlink()
        with self.assertRaisesRegex(UserError, "stock operations cannot be deleted"):
            picking.unlink()

        production = self.env["mrp.production"].sudo().with_context(**context).create(
            {
                "product_id": product.id,
                "product_qty": 1,
                "uom_id": product.uom_id.id,
                "location_src_id": warehouse.lot_stock_id.id,
                "location_dest_id": warehouse.lot_stock_id.id,
                "usl_b2c_source_key": "test:historical-production",
            },
        )
        with self.assertRaisesRegex(UserError, "production orders cannot be deleted"):
            production.sudo().with_context({}).unlink()

        unbuild = self.env["mrp.unbuild"].sudo().with_context(**context).create(
            {
                "product_id": product.id,
                "product_qty": 1,
                "uom_id": product.uom_id.id,
                "company_id": self.company.id,
                "location_id": warehouse.lot_stock_id.id,
                "location_dest_id": warehouse.lot_stock_id.id,
                "usl_historical_b2c": True,
                "usl_b2c_source_key": "test:historical-unbuild",
            },
        )
        with self.assertRaisesRegex(UserError, "conversions cannot be deleted"):
            unbuild.sudo().with_context({}).unlink()

        landed_cost = self.env["stock.landed.cost"].sudo().with_context(**context).create(
            {
                "company_id": self.company.id,
                "usl_historical_b2c": True,
                "usl_b2c_source_key": "test:historical-landed-cost",
            },
        )
        cost_line = self.env["stock.landed.cost.lines"].sudo().with_context(
            **context
        ).create(
            {
                "name": "Historical freight",
                "cost_id": landed_cost.id,
                "product_id": product.id,
                "price_unit": 4,
                "split_method": "by_quantity",
            },
        )
        adjustment_line = self.env["stock.valuation.adjustment.lines"].sudo().with_context(
            **context
        ).create(
            {
                "cost_id": landed_cost.id,
                "cost_line_id": cost_line.id,
                "move_id": move.id,
                "product_id": product.id,
                "quantity": 1,
                "former_cost": 5,
                "additional_landed_cost": 4,
            },
        )
        landed_cost.sudo().with_context(**context).write({"state": "done"})
        with self.assertRaisesRegex(UserError, "landed cost lines are locked"):
            cost_line.sudo().with_context({}).write({"price_unit": 5})
        with self.assertRaisesRegex(UserError, "landed cost lines cannot be deleted"):
            cost_line.sudo().with_context({}).unlink()
        with self.assertRaisesRegex(UserError, "valuation adjustments are locked"):
            adjustment_line.sudo().with_context({}).write(
                {"additional_landed_cost": 5},
            )
        with self.assertRaisesRegex(UserError, "valuation adjustments cannot be deleted"):
            adjustment_line.sudo().with_context({}).unlink()
        with self.assertRaisesRegex(UserError, "landed costs cannot be deleted"):
            landed_cost.sudo().with_context({}).unlink()

    def test_historical_customer_communication_setting_requires_system_admin(self):
        field = self.env["res.config.settings"]._fields[
            "allow_historical_b2c_customer_communication"
        ]
        self.assertEqual(field.groups, "base.group_system")
        self.assertFalse(self.manager.has_group("base.group_system"))

    def test_provider_contact_identity_is_immutable_and_company_scoped(self):
        context = {
            HISTORICAL_B2C_MATERIALIZATION_CONTEXT: MATERIALIZATION_TOKEN,
        }
        partner = self.env["res.partner"].sudo().with_context(**context).create(
            {
                "name": "Other-company historical recipient",
                "company_id": self.other_company.id,
                "usl_historical_b2c_contact": True,
            },
        )
        identity = self.env["b2c.partner.identity"].sudo().with_context(
            **context
        ).create(
            {
                "name": partner.name,
                "company_id": self.other_company.id,
                "source_provider": "etsy",
                "identity_role": "delivery",
                "identity_digest": "d" * 64,
                "partner_id": partner.id,
            },
        )
        visible = (
            self.env["b2c.partner.identity"]
            .with_user(self.reader)
            .with_context(allowed_company_ids=[self.company.id])
            .search([("id", "=", identity.id)])
        )
        self.assertFalse(visible)
        with self.assertRaises(AccessError):
            identity.with_user(self.manager).write({"name": "Changed"})
        with self.assertRaises(AccessError):
            self.env["b2c.partner.identity"].with_user(self.manager).create(
                {
                    "name": "Forged",
                    "company_id": self.company.id,
                    "source_provider": "etsy",
                    "identity_role": "delivery",
                    "identity_digest": "e" * 64,
                    "partner_id": self.company.partner_id.id,
                },
            )

    def test_raw_evidence_is_access_scoped_and_immutable(self):
        self.assertEqual(
            self.evidence.with_user(self.manager).payload_json,
            {"fixture": True},
        )
        with self.assertRaises(AccessError):
            self.evidence.with_user(self.reader).read(["payload_json"])
        with self.assertRaises(AccessError):
            self.evidence.with_user(self.manager).write(
                {"payload_json": {"fixture": False}},
            )
        with self.assertRaises(AccessError):
            self.env["b2c.provider.evidence"].with_user(self.manager).create(
                {
                    "evidence_key": "manual-create",
                    "source_provider": "manual",
                    "source_name": "forbidden",
                    "source_checksum": "d" * 64,
                    "schema_digest": "e" * 64,
                    "payload_digest": "f" * 64,
                    "payload_json": {},
                },
            )

    def test_sku_mapping_requires_explicit_review(self):
        product = self.env["product.product"].create(
            {"name": "Mapped product", "default_code": "ODOO-1"},
        )
        alias = self.env["b2c.product.alias"].create(
            {
                "company_id": self.company.id,
                "channel_id": self.channel.id,
                "source_provider": "etsy",
                "original_sku": "SOURCE-1",
                "suggested_product_id": product.id,
                "mapping_state": "pending",
            },
        )
        self.assertFalse(alias.product_id)
        alias.with_user(self.operator).action_verify()
        self.assertEqual(alias.product_id, product)
        self.assertEqual(alias.mapping_state, "verified")
        alias.with_user(self.operator).action_reject()
        self.assertFalse(alias.product_id)
        self.assertEqual(alias.mapping_state, "rejected")
        with self.assertRaises(ValidationError):
            alias.with_user(self.operator).write(
                {"mapping_state": "verified", "product_id": False},
            )

    def test_monthly_grains_locking_and_no_native_side_effects(self):
        protected_models = (
            "account.move",
            "account.move.line",
            "account.partial.reconcile",
            "account.full.reconcile",
            "stock.picking",
            "stock.move",
            "stock.move.line",
            "stock.quant",
        )
        before = {
            model: self.env[model].sudo().search_count([])
            for model in protected_models
        }
        order = self.env["b2c.order"].create(self._order_values("session"))
        alias = self.env["b2c.product.alias"].create(
            {
                "company_id": self.company.id,
                "channel_id": self.channel.id,
                "source_provider": "manual",
                "original_sku": "UNMAPPED",
            },
        )
        self.env["b2c.order.line"].create(
            {
                "order_id": order.id,
                "line_key": "manual:line:1",
                "original_sku": "UNMAPPED",
                "original_name": "Source item",
                "quantity": 2,
                "revenue_amount": 80,
                "revenue_company_amount": 80,
                "alias_id": alias.id,
                "mapping_state": "pending",
                "amount_completeness": "partial",
            },
        )
        self.env["b2c.payment.event"].create(
            {
                "name": "Refund and fee",
                "company_id": self.company.id,
                "channel_id": self.channel.id,
                "order_id": order.id,
                "source_provider": "manual",
                "provider_event_key": "manual:refund:1",
                "event_type": "refund",
                "state": "refunded",
                "event_date": datetime(2026, 8, 5, 10, 0),
                "currency_id": self.company.currency_id.id,
                "amount": -10,
                "refund_amount": -10,
                "fee_amount": 3,
                "net_amount": -13,
                "company_amount": -10,
                "refund_company_amount": -10,
                "fee_company_amount": 3,
                "net_company_amount": -13,
                "conversion_state": "not_needed",
                "completeness_state": "complete",
                "mapping_state": "not_applicable",
                "review_state": "reviewed",
            },
        )
        self.env["b2c.fulfilment.event"].create(
            {
                "name": "Own-stock COGS evidence",
                "company_id": self.company.id,
                "channel_id": self.channel.id,
                "order_id": order.id,
                "source_provider": "manual",
                "provider_event_key": "manual:fulfilment:1",
                "state": "fulfilled",
                "fulfilment_mode": "own_stock",
                "event_date": datetime(2026, 8, 6, 10, 0),
                "currency_id": self.company.currency_id.id,
                "cogs_amount": 35,
                "company_cogs_amount": 35,
                "conversion_state": "pending",
                "completeness_state": "complete",
                "review_state": "reviewed",
            },
        )
        attachment = self.env["ir.attachment"].create(
            {"name": "synthetic evidence", "raw": b"test"},
        )
        self.env["b2c.accounting.link"].create(
            {
                "name": "Order support",
                "company_id": self.company.id,
                "link_type": "supporting",
                "link_state": "verified",
                "order_id": order.id,
                "attachment_id": attachment.id,
            },
        )
        order.accounting_link_state = "partial"
        session = self.env["b2c.accounting.session"].create(
            {
                "company_id": self.company.id,
                "channel_id": self.channel.id,
                "period_start": "2026-08-01",
            },
        )
        session.action_refresh()
        self.assertEqual(session.order_count, 1)
        self.assertEqual(session.units_sold, 2)
        self.assertEqual(session.revenue_company_amount, 120)
        self.assertEqual(session.refund_company_amount, -10)
        self.assertEqual(session.fee_company_amount, 3)
        self.assertEqual(session.cogs_company_amount, 35)
        self.assertEqual(session.gross_margin_company_amount, 72)
        self.assertEqual(session.unallocated_revenue_company_amount, 40)
        self.assertAlmostEqual(session.line_revenue_coverage_percent, 66.6666667)
        self.assertEqual(session.direct_accounting_link_count, 0)
        self.assertEqual(session.aggregate_covered_count, 1)
        self.assertEqual(session.not_applicable_link_count, 0)
        self.assertAlmostEqual(session.accounting_link_coverage_percent, 100 / 3)
        self.assertEqual(session.direct_accounting_link_coverage_percent, 0)
        self.assertEqual(session.pending_mapping_count, 1)
        self.assertEqual(session.pending_link_count, 2)
        self.assertEqual(session.pending_conversion_count, 1)
        session.action_mark_reviewed()
        session.action_lock()
        with self.assertRaises(UserError):
            session.write({"review_note": "locked"})
        with self.assertRaises(UserError):
            self.env["b2c.accounting.link"].create(
                {
                    "name": "Late locked-session evidence",
                    "company_id": self.company.id,
                    "link_type": "supporting",
                    "link_state": "pending",
                    "session_id": session.id,
                    "attachment_id": attachment.id,
                },
            )
        unlocked_link = self.env["b2c.accounting.link"].create(
            {
                "name": "Unscoped evidence",
                "company_id": self.company.id,
                "link_type": "revenue",
                "link_state": "pending",
                "order_id": order.id,
                "attachment_id": attachment.id,
            },
        )
        with self.assertRaises(UserError):
            unlocked_link.write({"session_id": session.id})
        with self.assertRaises(AccessError):
            session.with_user(self.operator).action_unlock()
        if self.irreversible_manager != self.manager:
            with self.assertRaises(AccessError):
                session.with_user(self.manager).action_unlock()
        session.with_user(self.irreversible_manager).action_unlock()

        after = {
            model: self.env[model].sudo().search_count([])
            for model in protected_models
        }
        self.assertEqual(before, after)

    def test_refunds_must_remain_negative(self):
        values = self._order_values("positive-refund")
        values["refund_amount"] = 10
        with self.assertRaises(ValidationError):
            self.env["b2c.order"].create(values)
        with self.assertRaises(ValidationError):
            self.env["b2c.payment.event"].create(
                {
                    "name": "Invalid refund",
                    "company_id": self.company.id,
                    "channel_id": self.channel.id,
                    "source_provider": "manual",
                    "provider_event_key": "manual:positive-refund",
                    "event_type": "refund",
                    "event_date": datetime(2026, 8, 5, 10, 0),
                    "currency_id": self.company.currency_id.id,
                    "amount": 10,
                    "refund_amount": 10,
                },
            )
