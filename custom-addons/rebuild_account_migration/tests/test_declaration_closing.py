import base64
import io
import json
import zipfile
from datetime import date
from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged


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

    def test_reviewer_scope_and_evidence_backed_decisions(self):
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

        missing_evidence = self.env["rebuild.account.review.decision"].with_user(reviewer).create({
            "gate": "declaration_review",
            "conclusion": "accepted",
            "required_authority": "accountant",
            "company_id": company.id,
            "declaration_id": declaration.id,
            "decision_summary": "The declaration was reviewed.",
        })
        with self.assertRaisesRegex(UserError, "Record the evidence"):
            missing_evidence.with_user(reviewer).action_record()

        declaration_decision = self.env["rebuild.account.review.decision"].with_user(reviewer).create({
            "gate": "declaration_review",
            "conclusion": "accepted_with_difference",
            "required_authority": "accountant",
            "company_id": company.id,
            "declaration_id": declaration.id,
            "decision_summary": "Reviewed with the documented external administrative confirmations.",
            "evidence_summary": "Reviewer package REF-DECL-UNIT and declaration field schedule.",
        })
        declaration_decision.with_user(reviewer).action_record()
        self.assertEqual(declaration.review_status, "accepted_with_difference")
        self.assertEqual(declaration.status, "ready_to_file")

        closing_decision = self.env["rebuild.account.review.decision"].with_user(reviewer).create({
            "gate": "closing_review",
            "conclusion": "accepted",
            "required_authority": "accountant",
            "company_id": company.id,
            "closing_period_id": closing.id,
            "decision_summary": "The package was reviewed, but the automated blocker still controls closure.",
            "evidence_summary": "Reviewer package REF-CLOSE-UNIT.",
        })
        closing_decision.with_user(reviewer).action_record()
        self.assertEqual(closing.review_status, "accepted")
        self.assertEqual(closing.state, "blocked")

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
            "review_status": "accepted",
            "package_reference": "REF-CLOSE-2026-01",
        })

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
        self.assertEqual(json.loads(closing.final_lock_dates)["fiscalyear_lock_date"], "2026-01-31")

        wizard = self.env["rebuild.account.report.export.wizard"].with_company(company).create({
            "company_id": company.id,
            "report_type": "closing_package",
            "date_from": closing.date_from,
            "date_to": closing.date_to,
            "target_move": "posted",
            "export_format": "xlsx",
        })
        rows = wizard._closing_package_rows()
        self.assertIn("Closing overview", {row["section"] for row in rows})
        self.assertIn("Lock dates", {row["section"] for row in rows})
        wizard.action_generate_export()
        xlsx_payload = base64.b64decode(wizard.export_file)
        self.assertTrue(xlsx_payload.startswith(b"PK"))
        self.assertGreater(len(xlsx_payload), 10_000)
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
