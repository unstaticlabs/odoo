from datetime import datetime

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged


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
        session.with_user(self.manager).action_unlock()

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
