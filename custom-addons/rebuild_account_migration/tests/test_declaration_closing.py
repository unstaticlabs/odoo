import base64
import hashlib
import io
import json
import zipfile
from datetime import date
from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged

from odoo.addons.rebuild_account_migration.models.closing import (
    CLOSING_CONTROL_DEFINITIONS,
    HYGIENE_CONTROL_DEFINITIONS,
)


@tagged("post_install", "-at_install", "rebuild_account_migration_unit")
class TestDeclarationAndClosing(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.france = cls.env.ref("base.fr")
        cls.euro = cls.env.ref("base.EUR")
        cls.reviewer_group = cls.env.ref(
            "rebuild_account_migration.group_rebuild_accountant_reviewer",
        )

    def _company(self, name="Declaration Test Company", profile=True):
        vals = {
            "name": name,
            "currency_id": self.euro.id,
            "country_id": self.france.id,
            "account_fiscal_country_id": self.france.id,
            "fiscalyear_last_day": 30,
            "fiscalyear_last_month": "9",
        }
        if profile:
            vals.update({
                "rebuild_declaration_profile_active": True,
                "rebuild_legal_form": "sasu",
                "rebuild_corporate_tax_regime": "is",
                "rebuild_profit_tax_regime": "bic_simplified",
                "rebuild_vat_regime": "simplified",
                "rebuild_first_fiscalyear_start": "2024-01-10",
                "rebuild_declaration_profile_evidence": "Unit-test confirmed French SASU profile.",
            })
        return self.env["res.company"].create(vals)

    def _declaration(self, company, rule_xmlid="declaration_rule_3517_2026"):
        rule = self.env.ref(f"rebuild_account_migration.{rule_xmlid}")
        return self.env["rebuild.account.declaration"].with_company(company).create({
            "name": "Unit declaration",
            "company_id": company.id,
            "rule_id": rule.id,
            "period_start": "2025-10-01",
            "period_end": "2026-09-30",
            "fiscalyear_start": "2025-10-01",
            "fiscalyear_end": "2026-09-30",
            "deadline_date": "2026-12-31",
            "deadline_basis": "Unit-test deadline basis.",
            "applicability_reason": "Unit-test confirmed profile.",
        })

    def _account(self, company, code, name, account_type, reconcile=False):
        return self.env["account.account"].with_company(company).create({
            "code": code,
            "name": name,
            "account_type": account_type,
            "reconcile": reconcile,
            "company_ids": [Command.set([company.id])],
        })

    def _post_vat_move(self, company, journal, vat_account, offset_account, move_date, ref, debit, credit):
        move = self.env["account.move"].with_company(company).create({
            "move_type": "entry",
            "company_id": company.id,
            "journal_id": journal.id,
            "date": move_date,
            "ref": ref,
            "line_ids": [
                Command.create({
                    "name": ref,
                    "account_id": vat_account.id,
                    "debit": debit,
                    "credit": credit,
                }),
                Command.create({
                    "name": ref,
                    "account_id": offset_account.id,
                    "debit": credit,
                    "credit": debit,
                }),
            ],
        })
        move.action_post()
        return move

    def test_versioned_rules_deadlines_and_idempotent_profile_sync(self):
        company = self._company()
        company.fiscalyear_lock_date = fields.Date.from_string("2025-09-30")
        Declaration = self.env["rebuild.account.declaration"]

        self.assertEqual(
            Declaration._is_instalment_deadlines(date(2025, 9, 30)),
            [date(2024, 12, 15), date(2025, 3, 15), date(2025, 6, 15), date(2025, 9, 15)],
        )
        self.assertEqual(Declaration._annual_deadline("FR_2572", date(2025, 9, 30)), date(2026, 1, 15))
        self.assertEqual(Declaration._annual_deadline("FR_2065", date(2025, 9, 30)), date(2026, 1, 15))
        self.assertEqual(Declaration._annual_deadline("FR_3517_S", date(2025, 9, 30)), date(2025, 12, 31))

        with patch.object(fields.Date, "context_today", return_value=date(2026, 7, 22)):
            first = Declaration.sync_for_company(company)
            second = Declaration.sync_for_company(company)

        self.assertEqual(len(first), 21)
        self.assertEqual(second, first)
        self.assertEqual(
            Declaration.search_count([("company_id", "=", company.id)]),
            21,
        )
        self.assertFalse(first.filtered(lambda item: item.rule_id.code in {"FR_2069_RCI", "FR_RCM_2777"}))
        self.assertEqual(set(first.mapped("rule_version")), {"2025", "2026"})

        instalments = first.filtered(lambda item: item.rule_id.code == "FR_3514")
        refund_codes = {
            "USL_CA12_REFUND_ACCEPTED",
            "USL_CA12_LATER_REFUND",
            "USL_CA12_REMAINING_CREDIT",
        }
        self.assertEqual(len(instalments), 5)
        self.assertFalse(instalments.field_line_ids.filtered(lambda line: line.field_code in refund_codes))
        self.assertEqual(set(instalments.mapped("payment_status")), {"not_due"})

        ca12 = first.filtered(lambda item: item.rule_id.code == "FR_3517_S")
        self.assertEqual(len(ca12), 2)
        self.assertEqual(len(ca12.field_line_ids.filtered(lambda line: line.field_code in refund_codes)), 6)
        self.assertEqual(
            set(ca12.field_line_ids.filtered(
                lambda line: line.field_code == "USL_CA12_942_LEDGER_CLASSIFICATION",
            ).mapped("validation_status")),
            {"matched"},
        )

        first_year_is = first.filtered(
            lambda item: item.rule_id.code == "FR_2571" and item.fiscalyear_end == date(2025, 9, 30),
        )
        self.assertEqual(set(first_year_is.field_line_ids.mapped("amount")), {0.0})
        self.assertTrue(all(
            "No prior fiscal-year" in reference
            for reference in first_year_is.field_line_ids.mapped("source_reference")
        ))

    def test_reviewer_scope_and_manager_evidence_backed_decisions(self):
        company = self._company("Reviewer Scope Company")
        declaration = self._declaration(company)
        closing = self.env["rebuild.account.closing.period"].with_company(company).create({
            "name": "Blocked reviewer close",
            "company_id": company.id,
            "period_type": "annual",
            "date_from": "2025-10-01",
            "date_to": "2026-09-30",
            "fiscalyear_start": "2025-10-01",
            "fiscalyear_end": "2026-09-30",
        })
        self.env["rebuild.account.closing.control"].create({
            "closing_period_id": closing.id,
            "code": "unit_blocker",
            "category": "accounting",
            "name": "Unit blocker",
            "status": "block",
            "summary": "A unit-test blocker remains.",
            "next_action": "Resolve the unit-test blocker.",
        })
        reviewer = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Declaration Reviewer",
            "login": "declaration.reviewer@example.invalid",
            "email": "declaration.reviewer@example.invalid",
            "company_id": company.id,
            "company_ids": [Command.set([company.id])],
            "group_ids": [Command.set([self.reviewer_group.id])],
        })

        self.assertEqual(declaration.with_user(reviewer).read(["name"])[0]["name"], declaration.name)
        self.assertEqual(closing.with_user(reviewer).read(["name"])[0]["name"], closing.name)
        with self.assertRaises(AccessError):
            declaration.with_user(reviewer).write({"status": "ready_to_file"})
        with self.assertRaises(AccessError):
            closing.with_user(reviewer).write({"state": "ready"})

        decision_model = self.env["rebuild.account.review.decision"]
        self.assertTrue(decision_model.with_user(reviewer).has_access("read"))
        self.assertFalse(decision_model.with_user(reviewer).has_access("write"))
        self.assertFalse(decision_model.with_user(reviewer).has_access("create"))
        with self.assertRaises(AccessError):
            decision_model.with_user(reviewer).create({
                "gate": "declaration_review",
                "conclusion": "accepted",
                "required_authority": "accountant",
                "company_id": company.id,
                "declaration_id": declaration.id,
                "decision_summary": "Reviewer cannot create decisions.",
            })

        missing_evidence = decision_model.create({
            "gate": "declaration_review",
            "conclusion": "accepted",
            "required_authority": "accountant",
            "company_id": company.id,
            "declaration_id": declaration.id,
            "decision_summary": "The declaration was reviewed.",
        })
        with self.assertRaisesRegex(UserError, "Record the evidence"):
            missing_evidence.action_record()

        declaration_decision = decision_model.create({
            "gate": "declaration_review",
            "conclusion": "accepted_with_difference",
            "required_authority": "accountant",
            "company_id": company.id,
            "declaration_id": declaration.id,
            "decision_summary": "Reviewed with the documented external administrative confirmations.",
            "evidence_summary": "Reviewer package REF-DECL-UNIT and declaration field schedule.",
        })
        declaration_decision.action_record()
        self.assertEqual(declaration.review_status, "accepted_with_difference")
        self.assertEqual(declaration.status, "ready_to_file")

        not_applicable_closing = decision_model.create({
            "gate": "closing_review",
            "conclusion": "not_applicable",
            "required_authority": "accountant",
            "company_id": company.id,
            "closing_period_id": closing.id,
            "decision_summary": "This conclusion does not accept a closing package.",
            "evidence_summary": "No package acceptance was granted.",
        })
        not_applicable_closing.action_record()
        self.assertEqual(closing.review_status, "rejected")
        self.assertEqual(closing.state, "blocked")
        self.assertFalse(closing.snapshot_ids)

        package_payload = b"reviewer closing package"
        package_attachment = self.env["ir.attachment"].create({
            "name": "reviewer-closing-package.pdf",
            "type": "binary",
            "datas": base64.b64encode(package_payload),
            "mimetype": "application/pdf",
            "res_model": closing._name,
            "res_id": closing.id,
        })
        closing.package_attachment_ids = [Command.link(package_attachment.id)]
        closing_decision = decision_model.create({
            "gate": "closing_review",
            "conclusion": "accepted",
            "required_authority": "accountant",
            "company_id": company.id,
            "closing_period_id": closing.id,
            "decision_summary": "The package was reviewed, but the automated blocker still controls closure.",
            "evidence_summary": "Reviewer package REF-CLOSE-UNIT.",
        })
        closing_decision.action_record()
        self.assertEqual(closing.review_status, "accepted")
        self.assertEqual(closing.state, "blocked")
        self.assertEqual(closing.snapshot_count, 1)
        snapshot = closing.snapshot_ids
        self.assertEqual(
            snapshot.sha256,
            hashlib.sha256(package_payload).hexdigest(),
        )
        self.assertEqual(base64.b64decode(snapshot.payload), package_payload)
        self.assertEqual(
            snapshot.with_user(reviewer).read(["sha256"])[0]["sha256"],
            snapshot.sha256,
        )
        self.assertFalse(snapshot.with_user(reviewer).has_access("write"))
        with self.assertRaisesRegex(UserError, "immutable"):
            snapshot.with_user(reviewer).write({"name": "Changed"})
        with self.assertRaisesRegex(UserError, "immutable"):
            snapshot.write({"name": "Changed"})
        with self.assertRaisesRegex(UserError, "locked"):
            closing.write({"package_reference": "Changed after acceptance"})
        with self.assertRaisesRegex(UserError, "locked"):
            package_attachment.write({
                "datas": base64.b64encode(b"changed package"),
            })
        closing_decision.action_supersede()
        self.assertEqual(closing.review_status, "accountant_requested")
        self.assertEqual(closing.state, "blocked")
        closing.write({"package_reference": "New review cycle"})
        package_attachment.write({"name": "new-review-cycle.pdf"})
        self.assertEqual(closing.package_reference, "New review cycle")
        self.assertEqual(package_attachment.name, "new-review-cycle.pdf")

    def test_accounting_manager_can_complete_internal_approval_without_reviewer(self):
        company = self._company("Internal Approval Company")
        declaration = self._declaration(company)
        declaration.write({
            "status": "internal_review",
            "review_status": "internal_ready",
        })
        with patch.object(
            type(declaration),
            "action_mark_internal_ready",
            return_value=True,
        ):
            declaration.action_mark_ready_to_file()
        self.assertEqual(declaration.status, "ready_to_file")
        self.assertEqual(declaration.review_status, "internal_ready")

        closing = self.env["rebuild.account.closing.period"].with_company(
            company,
        ).create({
            "name": "Internally approved close",
            "company_id": company.id,
            "period_type": "month",
            "date_from": "2026-01-01",
            "date_to": "2026-01-31",
            "fiscalyear_start": "2025-10-01",
            "fiscalyear_end": "2026-09-30",
            "state": "internal_review",
        })
        with patch.object(
            type(closing),
            "action_refresh_controls",
            return_value=True,
        ):
            closing.action_mark_ready_to_close()
        self.assertEqual(closing.state, "ready")
        self.assertEqual(closing.review_status, "internal_ready")

    def test_close_applies_soft_locks_and_exports_review_package(self):
        company = self._company("Closing Package Company", profile=False)
        closing = self.env["rebuild.account.closing.period"].with_company(company).create({
            "name": "January 2026 close",
            "company_id": company.id,
            "period_type": "month",
            "date_from": "2026-01-01",
            "date_to": "2026-01-31",
            "fiscalyear_start": "2025-10-01",
            "fiscalyear_end": "2026-09-30",
            "package_reference": "REF-CLOSE-2026-01",
        })

        wizard = self.env["rebuild.account.report.export.wizard"].with_company(company).create({
            "company_id": company.id,
            "closing_period_id": closing.id,
            "report_type": "closing_package",
            "date_from": closing.date_from,
            "date_to": closing.date_to,
            "target_move": "posted",
            "export_format": "xlsx",
        })
        closing_decision = self.env["rebuild.account.review.decision"].create({
            "gate": "closing_review",
            "conclusion": "accepted",
            "required_authority": "accountant",
            "company_id": company.id,
            "closing_period_id": closing.id,
            "decision_summary": "The generated closing package is accepted.",
            "evidence_summary": "XLSX and PDF packages reviewed in this test.",
        })
        with self.assertRaisesRegex(UserError, "Attach at least one"):
            closing_decision.action_record()

        rows = wizard._closing_package_rows()
        self.assertIn("Closing overview", {row["section"] for row in rows})
        self.assertIn("Lock dates", {row["section"] for row in rows})
        wizard.action_generate_export()
        xlsx_payload = base64.b64decode(wizard.export_file)
        self.assertTrue(xlsx_payload.startswith(b"PK"))
        self.assertGreater(len(xlsx_payload), 8_000)
        with zipfile.ZipFile(io.BytesIO(xlsx_payload)) as workbook_archive:
            workbook_xml = workbook_archive.read("xl/workbook.xml")
            shared_strings = workbook_archive.read("xl/sharedStrings.xml")
        self.assertIn(b'Metadata', workbook_xml)
        self.assertIn(b'Report', workbook_xml)
        self.assertIn(b'Audit Data', workbook_xml)
        self.assertIn(b'Closing Review Package', shared_strings)
        self.assertIn(b'Lock dates', shared_strings)

        wizard.export_format = "pdf"
        wizard.action_generate_export()
        pdf_payload = base64.b64decode(wizard.export_file)
        self.assertTrue(pdf_payload.startswith(b"%PDF"))
        self.assertGreater(len(pdf_payload), 10_000)
        self.assertIn(b"ReportLab PDF Library", pdf_payload)
        self.assertEqual(len(closing.package_attachment_ids), 2)

        closing_decision.action_record()
        self.assertEqual(closing.review_status, "accepted")
        self.assertEqual(closing.state, "ready")
        self.assertEqual(closing.snapshot_count, 2)
        self.assertEqual(
            set(closing.snapshot_ids.mapped("sha256")),
            {
                hashlib.sha256(
                    attachment.raw,
                ).hexdigest()
                for attachment in closing.package_attachment_ids
            },
        )
        self.assertEqual(
            {row["section"] for row in wizard._closing_package_rows()},
            {
                *{row["section"] for row in rows},
                "Accepted closing snapshots",
            },
        )

        closing.action_close_and_apply_lock_dates()

        self.assertEqual(closing.state, "closed")
        self.assertEqual(closing.readiness_status, "ready")
        for field_name in (
            "fiscalyear_lock_date",
            "tax_lock_date",
            "sale_lock_date",
            "purchase_lock_date",
        ):
            self.assertEqual(company[field_name], date(2026, 1, 31))
        self.assertFalse(company.hard_lock_date)
        self.assertEqual(
            json.loads(closing.final_lock_dates)["fiscalyear_lock_date"],
            "2026-01-31",
        )

    def test_unusual_balance_control_uses_natural_sides_and_account_policy(self):
        company = self._company("Unusual Balance Company", profile=False)
        liability = self._account(
            company,
            "401991",
            "Supplier advances to review",
            "liability_payable",
            reconcile=True,
        )
        cash = self._account(
            company,
            "512991",
            "Cash overdraft to review",
            "asset_cash",
        )
        contra_asset = self._account(
            company,
            "281991",
            "Accumulated depreciation",
            "asset_fixed",
        )
        expense = self._account(
            company,
            "606991",
            "Operating expense",
            "expense",
        )
        ignored_asset = self._account(
            company,
            "471991",
            "Documented two-sided clearing",
            "asset_current",
        )
        ignored_asset.rebuild_hygiene_balance_policy = "either"
        income = self._account(
            company,
            "766991",
            "Foreign exchange income",
            "income",
        )
        equity = self._account(
            company,
            "101991",
            "Unit balancing equity",
            "equity",
        )
        journal = self.env["account.journal"].with_company(company).create({
            "name": "Unusual balance tests",
            "code": "UBAL",
            "type": "general",
            "company_id": company.id,
        })

        def post_move(move_date, reference, lines):
            move = self.env["account.move"].with_company(company).create({
                "move_type": "entry",
                "company_id": company.id,
                "journal_id": journal.id,
                "date": move_date,
                "ref": reference,
                "line_ids": [
                    Command.create({
                        "name": reference,
                        "account_id": account.id,
                        "debit": debit,
                        "credit": credit,
                    })
                    for account, debit, credit in lines
                ],
            })
            move.action_post()

        post_move(
            "2026-01-15",
            "Wrong-way supplier and cash balances",
            [(liability, 100.0, 0.0), (cash, 0.0, 100.0)],
        )
        post_move(
            "2026-01-16",
            "Expected contra-asset balance",
            [(expense, 50.0, 0.0), (contra_asset, 0.0, 50.0)],
        )
        post_move(
            "2026-01-17",
            "Configured two-sided account",
            [(expense, 30.0, 0.0), (ignored_asset, 0.0, 30.0)],
        )
        post_move(
            "2026-01-18",
            "Current-year wrong-way income",
            [(income, 20.0, 0.0), (equity, 0.0, 20.0)],
        )
        post_move(
            "2025-09-15",
            "Prior-year income excluded from current close",
            [(income, 40.0, 0.0), (equity, 0.0, 40.0)],
        )
        closing = self.env["rebuild.account.closing.period"].with_company(
            company,
        ).create({
            "name": "January 2026 unusual balance review",
            "company_id": company.id,
            "period_type": "month",
            "date_from": "2026-01-01",
            "date_to": "2026-01-31",
            "fiscalyear_start": "2025-10-01",
            "fiscalyear_end": "2026-09-30",
        })

        unusual = closing._unusual_balance_rows()
        self.assertEqual(
            {account.id for account, _balance, _count, _side in unusual},
            {liability.id, cash.id, income.id},
        )
        control_values = closing._control_unusual_balances()
        self.assertEqual(control_values["status"], "warning")
        self.assertEqual(control_values["record_count"], 3)
        self.assertEqual(control_values["amount"], 220.0)
        self.assertEqual(control_values["owner"], "finance_operator")
        self.assertEqual(
            contra_asset._rebuild_hygiene_expected_balance_side(),
            "credit",
        )
        self.assertEqual(
            ignored_asset._rebuild_hygiene_expected_balance_side(),
            "either",
        )

        closing.action_refresh_controls()
        control = closing.control_line_ids.filtered(
            lambda line: line.code == "unusual_balances",
        )
        self.assertEqual(len(control), 1)
        action = control.action_open_records()
        self.assertEqual(action["res_model"], "account.move.line")
        account_domain = next(
            term
            for term in action["domain"]
            if isinstance(term, tuple) and term[0] == "account_id"
        )
        self.assertEqual(set(account_domain[2]), {liability.id, cash.id, income.id})
        self.assertEqual(action["context"]["search_default_group_by_account"], 1)
        self.env.flush_all()
        hygiene = self.env["rebuild.account.review.summary"].search([
            ("company_id", "=", company.id),
        ], limit=1)
        self.assertEqual(hygiene.unusual_balance_count, 3)
        self.assertEqual(hygiene.unusual_balance_amount, 220.0)
        self.assertGreaterEqual(hygiene.hygiene_attention_count, 3)
        hygiene_action = hygiene.action_open_unusual_balances()
        self.assertEqual(hygiene_action["res_model"], "account.move.line")

    def test_closing_controls_are_company_configurable(self):
        company = self._company("Configurable Closing Controls")
        closing = self.env["rebuild.account.closing.period"].with_company(
            company,
        ).create({
            "name": "Configurable January close",
            "company_id": company.id,
            "period_type": "month",
            "date_from": "2026-01-01",
            "date_to": "2026-01-31",
            "fiscalyear_start": "2025-10-01",
            "fiscalyear_end": "2026-09-30",
        })

        closing.action_refresh_controls()
        definitions = self.env[
            "rebuild.account.closing.control.definition"
        ].search([("company_id", "=", company.id)])
        self.assertEqual(
            {
                definition.code
                for definition in definitions.filtered("applies_to_closing")
            },
            {values[0] for values in CLOSING_CONTROL_DEFINITIONS},
        )
        self.assertEqual(
            {
                definition.code
                for definition in definitions.filtered("applies_to_hygiene")
            },
            {values[0] for values in HYGIENE_CONTROL_DEFINITIONS},
        )
        reviewer = self.env["res.users"].with_context(
            no_reset_password=True,
        ).create({
            "name": "Control Catalogue Reviewer",
            "login": "control.catalogue.reviewer@example.invalid",
            "email": "control.catalogue.reviewer@example.invalid",
            "company_id": company.id,
            "company_ids": [Command.set([company.id])],
            "group_ids": [Command.set([self.reviewer_group.id])],
        })
        self.assertTrue(definitions.with_user(reviewer).has_access("read"))
        self.assertFalse(definitions.with_user(reviewer).has_access("write"))
        with self.assertRaises(AccessError):
            definitions[:1].with_user(
                reviewer,
            ).action_refresh_open_workspaces()
        self.assertEqual(
            closing.control_line_ids.filtered(
                lambda line: line.code == "reports",
            ).status,
            "pass",
        )
        self.assertEqual(
            closing.control_line_ids.filtered(
                lambda line: line.code == "fec",
            ).status,
            "pass",
        )
        bank_definition = definitions.filtered(
            lambda definition: definition.code == "bank_reconciliation",
        )
        bank_definition.write({
            "enabled": False,
            "owner": "accountant_reviewer",
        })
        closing.action_refresh_controls()
        self.assertFalse(
            closing.control_line_ids.filtered(
                lambda line: line.code == "bank_reconciliation",
            ),
        )
        bank_definition.enabled = True
        closing.action_refresh_controls()
        bank_control = closing.control_line_ids.filtered(
            lambda line: line.code == "bank_reconciliation",
        )
        self.assertEqual(bank_control.owner, "accountant_reviewer")
        self.assertEqual(bank_control.definition_id, bank_definition)
        self.assertEqual(
            bank_control.definition_version,
            bank_definition.definition_version,
        )
        self.assertEqual(
            bank_control.definition_snapshot["code"],
            "bank_reconciliation",
        )
        self.assertEqual(bank_definition.closing_result_count, 1)
        self.assertEqual(bank_definition.origin, "company")

        bank_definition.write({
            "closing_period_scope": "annual",
            "impact_policy": "informational",
        })
        policy_result = bank_definition._apply_result_policy({
            "status": "block",
            "next_action": "Resolve the accounting condition.",
        })
        self.assertEqual(policy_result["status"], "info")
        closing.action_refresh_controls()
        self.assertFalse(
            closing.control_line_ids.filtered(
                lambda line: line.code == "bank_reconciliation",
            ),
        )

        with patch.object(
            type(closing),
            "_closing_control_evaluator_registry",
            return_value={},
        ):
            closing.action_refresh_controls()
        self.assertEqual(closing.readiness_status, "blocked")
        self.assertEqual(closing.state, "blocked")
        self.assertTrue(closing.technical_failure_count)
        technical_control = closing.control_line_ids.filtered(
            lambda line: line.status == "technical_error",
        )[0]
        self.assertEqual(technical_control.result_kind, "technical")
        self.assertEqual(
            technical_control.action_open_records()["res_model"],
            "rebuild.account.closing.control.definition",
        )

    def test_declaration_definitions_are_governed_and_company_versioned(self):
        company = self._company("Company Declaration Definitions")
        shared_rule = self.env.ref(
            "rebuild_account_migration.declaration_rule_3517_2026",
        )
        self.assertEqual(shared_rule.origin, "localization")
        self.assertFalse(shared_rule.company_id)
        self.assertTrue(shared_rule.business_purpose)
        with self.assertRaises(UserError):
            shared_rule.write({"name": "Unsafe direct customization"})

        action = shared_rule.with_company(
            company,
        ).action_customize_for_company()
        company_rule = self.env[
            "rebuild.account.declaration.rule"
        ].browse(action["res_id"])
        company_rule.write({
            "business_purpose": "Company CA12 filing obligation.",
            "filing_guidance": "Company-reviewed CA12 filing guidance.",
        })
        selected = self.env[
            "rebuild.account.declaration"
        ]._rules_for_period(company, date(2026, 9, 30))
        self.assertIn(company_rule, selected)
        self.assertNotIn(shared_rule, selected)

        declarations = self.env[
            "rebuild.account.declaration"
        ].with_company(company).sync_for_company(company)
        ca12 = declarations.filtered(
            lambda declaration: declaration.rule_id == company_rule,
        )
        self.assertTrue(ca12)
        self.assertEqual(
            ca12[0].definition_snapshot["id"],
            company_rule.id,
        )
        self.assertEqual(
            ca12[0].definition_snapshot["definition_version"],
            company_rule.version,
        )

    def test_hygiene_issues_link_sources_and_auto_resolve(self):
        company = self._company("Actionable Hygiene Company")
        expense_account = self._account(
            company,
            "606100",
            "Supplies",
            "expense",
        )
        payable_account = self._account(
            company,
            "401100",
            "Suppliers",
            "liability_payable",
            True,
        )
        journal = self.env["account.journal"].with_company(company).create({
            "name": "Hygiene Purchases",
            "code": "HYP",
            "type": "purchase",
            "company_id": company.id,
            "default_account_id": expense_account.id,
        })
        partner = self.env["res.partner"].with_company(company).create({
            "name": "Hygiene Supplier",
            "company_id": company.id,
            "property_account_payable_id": payable_account.id,
        })
        bill = self.env["account.move"].with_company(company).create({
            "move_type": "in_invoice",
            "company_id": company.id,
            "journal_id": journal.id,
            "partner_id": partner.id,
            "invoice_date": "2025-10-01",
            "date": "2025-10-01",
            "invoice_line_ids": [
                Command.create({
                    "name": "Office supplies",
                    "account_id": expense_account.id,
                    "quantity": 1,
                    "price_unit": 120.0,
                }),
            ],
        })

        Issue = self.env["rebuild.account.hygiene.issue"].with_company(company)
        Issue.sync_for_company(company)
        bill_issues = Issue.search([
            ("company_id", "=", company.id),
            ("target_model", "=", "account.move"),
            ("target_res_id", "=", bill.id),
            ("status", "=", "open"),
        ])
        self.assertEqual(
            set(bill_issues.mapped("issue_type")),
            {"draft", "evidence"},
        )
        evidence_issue = bill_issues.filtered(
            lambda issue: issue.issue_type == "evidence",
        )
        self.assertEqual(evidence_issue.confidence, "high")
        self.assertEqual(evidence_issue.owner_role, "finance_operator")
        self.assertEqual(
            evidence_issue.control_code,
            "hygiene_vendor_evidence",
        )
        self.assertEqual(
            evidence_issue.definition_id.code,
            "hygiene_vendor_evidence",
        )
        self.assertEqual(evidence_issue.definition_id.hygiene_result_count, 1)
        source_action = evidence_issue.action_open_source()
        self.assertEqual(source_action["res_model"], "account.move")
        self.assertEqual(source_action["res_id"], bill.id)

        evidence_definition = evidence_issue.definition_id
        evidence_definition.write({
            "impact_policy": "blocking",
            "owner": "accounting_manager",
        })
        Issue.sync_for_company(company)
        self.assertEqual(evidence_issue.severity, "1_blocking")
        self.assertEqual(evidence_issue.owner_role, "accounting_manager")
        self.assertEqual(evidence_definition.origin, "company")

        evidence_definition.impact_policy = "informational"
        Issue.sync_for_company(company)
        self.assertEqual(evidence_issue.severity, "4_information")

        evidence_definition.enabled = False
        Issue.sync_for_company(company)
        self.assertEqual(evidence_issue.status, "resolved")

        evidence_definition.enabled = True
        Issue.sync_for_company(company)
        self.assertEqual(evidence_issue.status, "open")

        bill.button_cancel()
        Issue.sync_for_company(company)
        self.assertFalse(
            Issue.search_count([
                ("id", "in", bill_issues.ids),
                ("status", "=", "open"),
            ]),
        )
        self.assertTrue(
            all(issue.resolved_at for issue in bill_issues),
        )

    def test_confirmed_vat_refund_reclassification_is_balanced_and_idempotent(self):
        company = self._company("VAT Reclassification Company")
        bank_account = self._account(company, "512TEST", "Test bank", "asset_cash")
        configured_suspense = self._account(company, "471999", "Configured suspense", "asset_current", True)
        imported_suspense = self._account(company, "471000", "Imported misclassification", "asset_current", True)
        vat_account = self._account(company, "445670", "VAT credit", "asset_current", True)
        offset_account = self._account(company, "100000", "Test offset", "equity")
        bank_journal = self.env["account.journal"].with_company(company).create({
            "name": "VAT refund bank",
            "code": "TVAT",
            "type": "bank",
            "company_id": company.id,
            "default_account_id": bank_account.id,
            "suspense_account_id": configured_suspense.id,
        })
        general_journal = self.env["account.journal"].with_company(company).create({
            "name": "VAT correction journal",
            "code": "TMIS",
            "type": "general",
            "company_id": company.id,
        })
        bank_line = self.env["account.bank.statement.line"].with_company(company).create({
            "journal_id": bank_journal.id,
            "date": "2026-07-17",
            "payment_ref": "REMB. DGFiP - unit test",
            "amount": 942.0,
            "rebuild_source_model": "account.bank.statement.line",
            "rebuild_source_id": 99942,
        })
        bank_line.move_id.button_draft()
        bank_line.move_id.line_ids.filtered(
            lambda line: line.account_id != bank_account,
        ).account_id = imported_suspense
        bank_line.move_id.action_post()
        self._post_vat_move(
            company,
            general_journal,
            vat_account,
            offset_account,
            "2025-09-30",
            "VAT credit before refunds",
            3442.0,
            0.0,
        )
        self._post_vat_move(
            company,
            general_journal,
            vat_account,
            offset_account,
            "2026-01-01",
            "Accepted €2,500 VAT refund",
            0.0,
            2500.0,
        )
        declaration = self._declaration(company)

        declaration.action_classify_confirmed_vat_refund()
        declaration.action_classify_confirmed_vat_refund()

        corrections = self.env["account.move"].with_company(company).search([
            ("company_id", "=", company.id),
            ("rebuild_source_model", "=", "account.move.usl_vat_refund_reclassification"),
        ])
        vat_lines = self.env["account.move.line"].with_company(company).search([
            ("company_id", "=", company.id),
            ("account_id", "=", vat_account.id),
            ("move_id.state", "=", "posted"),
        ])
        clearing_lines = (bank_line.move_id | corrections).line_ids.filtered(
            lambda line: line.account_id == imported_suspense,
        )

        self.assertEqual(len(corrections), 1)
        self.assertEqual(corrections.date, date(2026, 7, 17))
        self.assertEqual(sum(corrections.line_ids.mapped("balance")), 0.0)
        self.assertTrue(bank_line.is_reconciled)
        self.assertEqual(bank_line.amount_residual, 0.0)
        self.assertEqual(sum(vat_lines.mapped("amount_residual")), 0.0)
        self.assertTrue(all(vat_lines.mapped("reconciled")))
        self.assertEqual(sum(clearing_lines.mapped("amount_residual")), 0.0)
        self.assertTrue(all(clearing_lines.mapped("reconciled")))
        correction_partials = corrections.line_ids.mapped("matched_debit_ids") | corrections.line_ids.mapped("matched_credit_ids")
        self.assertTrue(correction_partials)
        self.assertEqual(
            len(corrections.line_ids.mapped("rebuild_source_id")),
            len(set(corrections.line_ids.mapped("rebuild_source_id"))),
        )
        self.assertEqual(
            len(correction_partials.mapped("rebuild_source_id")),
            len(set(correction_partials.mapped("rebuild_source_id"))),
        )
