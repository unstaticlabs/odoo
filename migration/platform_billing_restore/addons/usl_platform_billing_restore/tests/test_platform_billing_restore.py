import hashlib
from copy import deepcopy
from datetime import datetime

from odoo import Command, fields
from odoo.tests import tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.addons.usl_platform_billing_restore.models.restore import (
    BOOTSTRAP_SHA256,
    validate_source_identity,
)


@tagged("post_install", "-at_install", "usl_platform_billing_restore")
class TestPlatformBillingRestore(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.company_data["company"]
        cls.env.user.group_ids |= cls.env.ref(
            "usl_platform_billing.group_platform_billing_manager",
        )
        cls.partner = cls.partner_a
        cls.sale_journal = cls.company_data["default_journal_sale"]
        cls.purchase_journal = cls.company_data["default_journal_purchase"]
        cls.misc_journal = cls.company_data["default_journal_misc"]
        cls.currency = cls.company.currency_id
        cls.product_a.write(
            {
                "taxes_id": [Command.clear()],
                "supplier_taxes_id": [Command.clear()],
            },
        )
        cls.product_b.write(
            {
                "taxes_id": [Command.clear()],
                "supplier_taxes_id": [Command.clear()],
            },
        )
        cls.source = {
            "company": 9101,
            "partner": 9102,
            "revenue_product": 9103,
            "commission_product": 9104,
            "sale_journal": 9105,
            "purchase_journal": 9106,
            "misc_journal": 9107,
            "platform": 9108,
            "session": 9109,
            "payout": 9110,
            "invoice": 9111,
            "bill": 9112,
            "attachment": 9113,
            "currency": 9114,
            "user_partner": 9115,
        }
        cls._trace(cls.company, "res.company", cls.source["company"])
        cls._trace(cls.partner, "res.partner", cls.source["partner"])
        cls._trace(
            cls.env.user.partner_id,
            "res.partner",
            cls.source["user_partner"],
        )
        cls._trace(
            cls.product_a,
            "product.product",
            cls.source["revenue_product"],
        )
        cls._trace(
            cls.product_b,
            "product.product",
            cls.source["commission_product"],
        )
        cls._trace(
            cls.sale_journal,
            "account.journal",
            cls.source["sale_journal"],
        )
        cls._trace(
            cls.purchase_journal,
            "account.journal",
            cls.source["purchase_journal"],
        )
        cls._trace(
            cls.misc_journal,
            "account.journal",
            cls.source["misc_journal"],
        )
        cls.invoice = cls._draft_invoice(
            "out_invoice",
            cls.sale_journal,
            cls.product_a,
            100.0,
        )
        cls.bill = cls._draft_invoice(
            "in_invoice",
            cls.purchase_journal,
            cls.product_b,
            20.0,
        )
        cls._trace(cls.invoice, "account.move", cls.source["invoice"])
        cls._trace(cls.bill, "account.move", cls.source["bill"])
        cls.attachment = cls.env["ir.attachment"].create(
            {
                "name": "historical-evidence.txt",
                "raw": b"historical evidence",
            },
        )
        cls._trace(
            cls.attachment,
            "ir.attachment",
            cls.source["attachment"],
        )

    @classmethod
    def _trace(cls, record, source_model, source_id):
        record.write(
            {
                "rebuild_source_database": "synthetic_platform_source",
                "rebuild_source_model": source_model,
                "rebuild_source_id": source_id,
                "rebuild_source_snapshot": "synthetic-platform-r1",
                "rebuild_import_status": "imported",
            },
        )

    @classmethod
    def _draft_invoice(cls, move_type, journal, product, amount):
        return cls.env["account.move"].create(
            {
                "move_type": move_type,
                "company_id": cls.company.id,
                "journal_id": journal.id,
                "partner_id": cls.partner.id,
                "currency_id": cls.currency.id,
                "invoice_date": fields.Date.from_string("2026-07-31"),
                "invoice_line_ids": [
                    Command.create(
                        {
                            "product_id": product.id,
                            "quantity": 1,
                            "price_unit": amount,
                        },
                    ),
                ],
            },
        )

    def _payload(self):
        created = datetime(2026, 7, 1, 10, 30)
        written = datetime(2026, 7, 2, 11, 45)
        platform = {
            "id": self.source["platform"],
            "create_uid": self.env.user.id,
            "write_uid": self.env.user.id,
            "create_date": created,
            "write_date": written,
            "x_name": "Synthetic CreatorHub",
            "x_partner_id": self.source["partner"],
            "x_customer_partner_id": None,
            "x_supplier_partner_id": None,
            "x_commission_rate": 20.0,
            "x_currency_id": self.source["currency"],
            "x_revenue_product_id": self.source["revenue_product"],
            "x_commission_product_id": self.source["commission_product"],
            "x_sale_journal_id": self.source["sale_journal"],
            "x_purchase_journal_id": self.source["purchase_journal"],
            "x_compensation_journal_id": self.source["misc_journal"],
            "x_bank_journal_id": None,
            "x_bank_label_pattern": "SC payout {ref}",
            "x_bank_label_keywords": "SYNTHETIC",
            "x_bank_match_days_tolerance": 10,
            "x_bank_match_amount_tolerance": 1.0,
            "x_analytic_account_id": None,
            "x_analytic_plan_id": None,
            "x_analytic_distribution_json": None,
            "x_vendor_bill_grouping_mode": "monthly",
            "x_auto_post_invoices": False,
            "x_auto_create_compensation": True,
            "x_auto_reconcile_bank": False,
            "x_active": True,
        }
        session = {
            "id": self.source["session"],
            "create_uid": self.env.user.id,
            "write_uid": self.env.user.id,
            "create_date": created,
            "write_date": written,
            "x_name": "Synthetic July 2026",
            "x_company_id": self.source["company"],
            "x_period_month": fields.Date.from_string("2026-07-01"),
            "x_invoice_date": fields.Date.from_string("2026-07-31"),
            "x_due_date": fields.Date.from_string("2026-08-15"),
            "x_bank_currency_id": self.source["currency"],
            "x_state": "to_fix",
            "x_last_error": "Legacy error context",
            "x_validation_log": "Historical validation details",
            "x_warning_summary": None,
            "x_bank_reconcile_blocker": None,
            "x_generated_at": created,
            "x_generated_by_id": self.env.user.id,
        }
        payout = {
            "id": self.source["payout"],
            "create_uid": self.env.user.id,
            "write_uid": self.env.user.id,
            "create_date": created,
            "write_date": written,
            "x_session_id": self.source["session"],
            "x_platform_id": self.source["platform"],
            "x_payout_date": fields.Date.from_string("2026-07-15"),
            "x_platform_reference": "SYNTH-001",
            "x_platform_currency_id": self.source["currency"],
            "x_net_platform_amount": 80.0,
            "x_commission_rate_snapshot": 20.0,
            "x_bank_currency_id": self.source["currency"],
            "x_bank_received_amount": 80.0,
            "x_bank_statement_line_id": None,
            "x_bank_match_score": 100,
            "x_bank_match_status": "manual_required",
            "x_bank_amount_difference": 0.0,
            "x_bank_date_difference": 0,
            "x_bank_detection_reason": "Synthetic fixture",
            "x_customer_invoice_id": self.source["invoice"],
            "x_vendor_bill_id": self.source["bill"],
            "x_compensation_move_id": None,
            "x_state": "to_fix",
            "x_validation_status": "error",
            "x_validation_message": "Legacy issue",
        }
        moves = [
            {
                "id": self.source["invoice"],
                "name": self.invoice.name,
                "date": self.invoice.date,
                "state": self.invoice.state,
                "move_type": self.invoice.move_type,
                "currency_id": self.source["currency"],
                "x_content_billing_session_id": self.source["session"],
                "x_content_platform_id": self.source["platform"],
                "x_content_payout_refs": "SYNTH-001",
            },
            {
                "id": self.source["bill"],
                "name": self.bill.name,
                "date": self.bill.date,
                "state": self.bill.state,
                "move_type": self.bill.move_type,
                "currency_id": self.source["currency"],
                "x_content_billing_session_id": self.source["session"],
                "x_content_platform_id": self.source["platform"],
                "x_content_payout_refs": "SYNTH-001",
            },
        ]
        return {
            "platforms": [platform],
            "sessions": [session],
            "payouts": [payout],
            "moves": moves,
            "attachment_links": [
                {
                    "x_payout_line_id": self.source["payout"],
                    "attachment_id": self.source["attachment"],
                },
            ],
            "attachments": [
                {
                    "id": self.source["attachment"],
                    "name": self.attachment.name,
                    "checksum": hashlib.sha1(b"historical evidence").hexdigest(),
                    "file_size": len(b"historical evidence"),
                    "mimetype": "text/plain",
                },
            ],
            "currencies": [
                {
                    "id": self.source["currency"],
                    "name": self.currency.name,
                },
            ],
            "users": [
                {
                    "id": self.env.user.id,
                    "login": self.env.user.login,
                    "partner_id": self.source["user_partner"],
                    "company_id": self.source["company"],
                    "company_ids": [self.source["company"]],
                },
            ],
            "counts": {
                "platforms": 1,
                "sessions": 1,
                "payouts": 1,
                "moves": 2,
                "attachments": 1,
                "bank_candidates": 7,
            },
        }

    def _restore(self, payload):
        run = self.env["usl.platform.billing.restore.run"].create(
            {
                "source_database": "synthetic_platform_source",
                "source_snapshot": "synthetic-platform-r1",
                "source_dump_sha256": "synthetic-dump",
                "bootstrap_sha256": "synthetic-bootstrap",
                "target_database": self.env.cr.dbname,
            },
        )
        return run, run.restore_from_payload(payload)

    def test_source_identity_is_dump_bound_without_a_hardcoded_export(self):
        self.assertEqual(len(BOOTSTRAP_SHA256), 64)
        first = "a" * 64
        second = "b" * 64
        self.assertEqual(
            validate_source_identity(
                {"source_dump_sha256": first, "snapshot": f"source-{first[:12]}"},
            ),
            first,
        )
        self.assertEqual(
            validate_source_identity(
                {"source_dump_sha256": second, "snapshot": f"source-{second[:12]}"},
            ),
            second,
        )
        with self.assertRaisesRegex(RuntimeError, "not dump-bound"):
            validate_source_identity(
                {"source_dump_sha256": first, "snapshot": "source-wrong"},
            )

    def test_restore_links_history_and_is_idempotent(self):
        payload = self._payload()
        first, first_stats = self._restore(payload)
        self.assertEqual(first.status, "passed")
        platform = self.env["usl.platform.billing.platform"].search(
            [("rebuild_source_id", "=", self.source["platform"])],
        )
        session = self.env["usl.platform.billing.session"].search(
            [("rebuild_source_id", "=", self.source["session"])],
        )
        payout = self.env["usl.platform.billing.payout"].search(
            [("rebuild_source_id", "=", self.source["payout"])],
        )
        self.assertEqual(session.state, "generated")
        self.assertEqual(payout.state, "generated")
        self.assertEqual(payout.customer_invoice_id, self.invoice)
        self.assertEqual(payout.vendor_bill_id, self.bill)
        self.assertEqual(payout.attachment_ids, self.attachment)
        self.assertEqual(self.invoice.platform_billing_session_id, session)
        self.assertEqual(self.invoice.platform_billing_platform_id, platform)
        self.assertEqual(self.invoice.platform_billing_payout_ids, payout)
        self.assertEqual(session.create_date, payload["sessions"][0]["create_date"])
        imported_note = self.env["mail.message"].search(
            [
                ("model", "=", session._name),
                ("res_id", "=", session.id),
                ("subject", "ilike", "Imported platform billing history"),
            ],
        )
        self.assertEqual(len(imported_note), 1)
        self.assertIn("Legacy error context", imported_note.body)
        self.assertEqual(first_stats["source"]["bank_candidates"], 7)
        self.assertEqual(first_stats["target"]["bank_candidates"], 0)
        self.assertEqual(
            first_stats["ledger_digest_before"],
            first_stats["ledger_digest_after"],
        )

        second, second_stats = self._restore(payload)
        self.assertEqual(second.status, "passed")
        self.assertEqual(
            first_stats["canonical_digest"],
            second_stats["canonical_digest"],
        )
        self.assertEqual(
            self.env["usl.platform.billing.platform"].search_count(
                [("rebuild_source_id", "=", self.source["platform"])],
            ),
            1,
        )
        self.assertEqual(
            self.env["mail.message"].search_count(
                [
                    ("model", "=", session._name),
                    ("res_id", "=", session.id),
                    ("subject", "ilike", "Imported platform billing history"),
                ],
            ),
            1,
        )

    def test_restore_reenters_finalized_business_history(self):
        payload = self._payload()
        first, first_stats = self._restore(payload)
        self.assertEqual(first.status, "passed")
        records = [
            self.env["usl.platform.billing.platform"].sudo().search(
                [("rebuild_source_id", "=", self.source["platform"])],
            ),
            self.env["usl.platform.billing.session"].sudo().search(
                [("rebuild_source_id", "=", self.source["session"])],
            ),
            self.env["usl.platform.billing.payout"].sudo().search(
                [("rebuild_source_id", "=", self.source["payout"])],
            ),
        ]
        original_ids = {
            record._name: record.id
            for record in records
        }
        trace_values = {
            "rebuild_source_database": False,
            "rebuild_source_model": False,
            "rebuild_source_id": False,
            "rebuild_source_snapshot": False,
            "rebuild_import_status": False,
            "rebuild_import_note": False,
        }
        for record in records:
            record.write(trace_values)

        second, second_stats = self._restore(payload)

        self.assertEqual(second.status, "passed")
        self.assertEqual(
            first_stats["canonical_digest"],
            second_stats["canonical_digest"],
        )
        for model_name, record_id in original_ids.items():
            record = self.env[model_name].sudo().browse(record_id)
            self.assertTrue(record.exists())
            self.assertTrue(record.rebuild_source_id)
            self.assertEqual(
                self.env[model_name].sudo().search_count([]),
                1,
            )

    def test_missing_dependency_is_blocking(self):
        payload = self._payload()
        payload["platforms"][0]["x_partner_id"] = 999999
        run, _stats = self._restore(payload)
        self.assertEqual(run.status, "failed")
        self.assertTrue(
            run.issue_ids.filtered(
                lambda issue: (
                    issue.source_model == "res.partner"
                    and issue.source_id == 999999
                ),
            ),
        )
        self.assertFalse(
            self.env["usl.platform.billing.platform"].search(
                [("rebuild_source_id", "=", self.source["platform"])],
            ),
        )

    def test_duplicate_source_reference_is_blocking(self):
        payload = self._payload()
        duplicate = deepcopy(payload["payouts"][0])
        duplicate["id"] += 100
        payload["payouts"].append(duplicate)
        payload["counts"]["payouts"] = 2
        run, _stats = self._restore(payload)
        self.assertEqual(run.status, "failed")
        self.assertIn(
            "occurs 2 times",
            "\n".join(run.issue_ids.mapped("description")),
        )

    def test_pooled_source_bank_link_is_restored(self):
        payload = self._payload()
        bank_source_id = 999901
        statement = self.env["account.bank.statement"].create(
            {
                "name": "Synthetic pooled payout",
                "journal_id": self.company_data["default_journal_bank"].id,
                "date": fields.Date.from_string("2026-07-20"),
            },
        )
        bank_line = self.env["account.bank.statement.line"].create(
            {
                "name": "Synthetic pooled payout",
                "journal_id": self.company_data["default_journal_bank"].id,
                "statement_id": statement.id,
                "amount": 160.0,
                "date": fields.Date.from_string("2026-07-20"),
            },
        )
        self._trace(bank_line, "account.bank.statement.line", bank_source_id)
        payload["payouts"][0]["x_bank_statement_line_id"] = bank_source_id
        duplicate = deepcopy(payload["payouts"][0])
        duplicate["id"] += 100
        duplicate["x_platform_reference"] = "SYNTH-002"
        payload["payouts"].append(duplicate)
        payload["counts"]["payouts"] = 2

        run, _stats = self._restore(payload)

        self.assertEqual(run.status, "passed")
        restored = self.env["usl.platform.billing.payout"].search(
            [
                ("rebuild_source_id", "in", [self.source["payout"], duplicate["id"]]),
            ],
        )
        self.assertEqual(len(restored), 2)
        self.assertEqual(restored.bank_statement_line_ids, bank_line)
        self.assertEqual(
            self.env["usl.platform.billing.bank.allocation"].search_count(
                [("bank_statement_line_id", "=", bank_line.id)],
            ),
            2,
        )

    def test_overallocated_pooled_source_bank_link_is_blocking(self):
        payload = self._payload()
        bank_source_id = 999902
        statement = self.env["account.bank.statement"].create(
            {
                "name": "Synthetic overallocated payout",
                "journal_id": self.company_data["default_journal_bank"].id,
                "date": fields.Date.from_string("2026-07-20"),
            },
        )
        bank_line = self.env["account.bank.statement.line"].create(
            {
                "name": "Synthetic overallocated payout",
                "journal_id": self.company_data["default_journal_bank"].id,
                "statement_id": statement.id,
                "amount": 100.0,
                "date": fields.Date.from_string("2026-07-20"),
            },
        )
        self._trace(bank_line, "account.bank.statement.line", bank_source_id)
        payload["payouts"][0]["x_bank_statement_line_id"] = bank_source_id
        duplicate = deepcopy(payload["payouts"][0])
        duplicate["id"] += 100
        duplicate["x_platform_reference"] = "SYNTH-OVERALLOCATED"
        payload["payouts"].append(duplicate)

        run, _stats = self._restore(payload)

        self.assertEqual(run.status, "failed")
        self.assertIn(
            "allocations total",
            "\n".join(run.issue_ids.mapped("description")),
        )

    def test_reconciled_same_currency_payout_preserves_full_settlement(self):
        payload = self._payload()
        bank_source_id = 999904
        statement = self.env["account.bank.statement"].create(
            {
                "name": "Synthetic fee-adjusted payout",
                "journal_id": self.company_data["default_journal_bank"].id,
                "date": fields.Date.from_string("2026-07-20"),
            },
        )
        bank_line = self.env["account.bank.statement.line"].create(
            {
                "name": "Synthetic fee-adjusted payout",
                "journal_id": self.company_data["default_journal_bank"].id,
                "statement_id": statement.id,
                "amount": 72.52,
                "date": fields.Date.from_string("2026-07-20"),
            },
        )
        self._trace(bank_line, "account.bank.statement.line", bank_source_id)
        source_payout = payload["payouts"][0]
        source_payout.update(
            {
                "x_bank_statement_line_id": bank_source_id,
                "x_bank_received_amount": 72.52,
                "x_bank_match_status": "reconciled",
            },
        )

        first, _stats = self._restore(payload)

        self.assertEqual(first.status, "passed")
        payout = self.env["usl.platform.billing.payout"].search(
            [("rebuild_source_id", "=", self.source["payout"])],
        )
        self.assertEqual(payout.bank_allocation_ids.bank_amount, 72.52)
        self.assertEqual(payout.bank_allocation_ids.payout_amount, 80.0)

        payout.bank_allocation_ids.sudo().write({"payout_amount": 72.52})
        second, _stats = self._restore(payload)

        self.assertEqual(second.status, "passed")
        self.assertEqual(payout.bank_allocation_ids.payout_amount, 80.0)

    def test_missing_legacy_due_date_uses_native_payment_terms(self):
        payload = self._payload()
        payload["sessions"][0]["x_due_date"] = None

        first, first_stats = self._restore(payload)
        session = self.env["usl.platform.billing.session"].search(
            [("rebuild_source_id", "=", self.source["session"])],
        )
        second, second_stats = self._restore(payload)

        self.assertEqual(first.status, "passed")
        self.assertEqual(second.status, "passed")
        self.assertFalse(session.due_date)
        self.assertEqual(
            first_stats["canonical_digest"],
            second_stats["canonical_digest"],
        )

    def test_restores_missing_products_and_inactive_historical_user(self):
        payload = self._payload()
        revenue_source_id = 9201
        commission_source_id = 9202
        user_source_id = 9203
        payload["platforms"][0].update(
            {
                "x_revenue_product_id": revenue_source_id,
                "x_commission_product_id": commission_source_id,
                "create_uid": user_source_id,
                "write_uid": user_source_id,
            },
        )
        payload["sessions"][0].update(
            {
                "create_uid": user_source_id,
                "write_uid": user_source_id,
                "x_generated_by_id": user_source_id,
            },
        )
        payload["payouts"][0].update(
            {
                "create_uid": user_source_id,
                "write_uid": user_source_id,
            },
        )
        payload["products"] = [
            {
                "id": revenue_source_id,
                "name": {"en_US": "Imported platform revenue"},
                "default_code": "PLATFORM-REVENUE",
                "type": "service",
                "sale_ok": True,
                "purchase_ok": False,
                "active": True,
                "property_account_income_id": {},
                "property_account_expense_id": {},
                "uom_module": "uom",
                "uom_xml_name": "product_uom_unit",
                "category_module": "product",
                "category_xml_name": "product_category_services",
                "tax_ids": [],
                "supplier_tax_ids": [],
            },
            {
                "id": commission_source_id,
                "name": {"en_US": "Imported platform commission"},
                "default_code": "PLATFORM-COMMISSION",
                "type": "service",
                "sale_ok": False,
                "purchase_ok": True,
                "active": True,
                "property_account_income_id": {},
                "property_account_expense_id": {},
                "uom_module": "uom",
                "uom_xml_name": "product_uom_unit",
                "category_module": "product",
                "category_xml_name": "product_category_services",
                "tax_ids": [],
                "supplier_tax_ids": [],
            },
        ]
        payload["users"] = [
            {
                "id": user_source_id,
                "login": "historical-platform-user@example.invalid",
                "partner_id": self.source["partner"],
                "company_id": self.source["company"],
                "company_ids": [self.source["company"]],
            },
        ]

        run, _stats = self._restore(payload)

        self.assertEqual(run.status, "passed")
        products = (
            self.env["product.product"]
            .with_context(active_test=False)
            .search(
                [
                    ("rebuild_source_model", "=", "product.product"),
                    (
                        "rebuild_source_id",
                        "in",
                        [revenue_source_id, commission_source_id],
                    ),
                ],
            )
        )
        historical_user = (
            self.env["res.users"]
            .with_context(active_test=False)
            .search(
                [
                    (
                        "login",
                        "=",
                        "historical-platform-user@example.invalid",
                    ),
                ],
            )
        )
        session = self.env["usl.platform.billing.session"].search(
            [("rebuild_source_id", "=", self.source["session"])],
        )
        self.assertEqual(len(products), 2)
        self.assertFalse(historical_user.active)
        self.assertFalse(historical_user.group_ids)
        self.assertTrue(self.partner.active)
        self.assertEqual(session.generated_by_id, historical_user)
