import hashlib
import io
import json
import zipfile
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from lxml import etree

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged

from odoo.addons.rebuild_account_migration.models.closing import (
    CLOSING_CONTROL_DEFINITIONS,
    HYGIENE_CONTROL_DEFINITIONS,
)


RENDERED_PDF = b"%PDF-1.7\n" + (b"0" * 12_000) + b"\n%%EOF\n"
RENDER_RESULT = {
    "pdf": RENDERED_PDF,
    "template_revision": "reviewed-template",
    "payload_sha256": "c" * 64,
    "renderer_version": "1.0.0",
}
COMPANY_PAYLOAD = {
    "name": "Rendered company",
    "legal_identity_lines": ["SAS · RCS Paris"],
    "primary_color": "714B67",
    "footer_label": "Rendered company",
}


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
                "rebuild_vat_transition_date": "2027-10-01",
                "rebuild_first_fiscalyear_start": "2024-01-10",
                "rebuild_first_fiscalyear_end": "2025-09-30",
                "rebuild_declaration_profile_evidence": "Unit-test confirmed French SASU profile.",
            })
        return self.env["res.company"].create(vals)

    def test_declarations_support_chatter_and_retired_workflow_category(self):
        Rule = self.env["rebuild.account.declaration.rule"]
        Declaration = self.env["rebuild.account.declaration"]

        self.assertIn("message_ids", Rule._fields)
        self.assertIn("message_ids", Declaration._fields)
        self.assertIn("legacy", dict(Rule._fields["category"].selection))
        for field_name in (
            "deadline_date", "status", "validation_status", "review_status",
            "preparation_status", "filing_status", "payment_status",
            "acceptance_status", "amount_due",
        ):
            self.assertTrue(Declaration._fields[field_name].tracking)

    def test_declaration_views_distinguish_automatic_and_user_managed_statuses(self):
        Declaration = self.env["rebuild.account.declaration"]
        for field_name in (
            "applicability",
            "status",
            "validation_status",
            "preparation_status",
            "review_status",
            "filing_status",
        ):
            self.assertTrue(Declaration._fields[field_name].readonly)
        self.assertFalse(Declaration._fields["acceptance_status"].readonly)
        self.assertFalse(Declaration._fields["payment_status"].readonly)

        list_arch = etree.fromstring(
            self.env.ref(
                "rebuild_account_migration.view_rebuild_account_declaration_list",
            ).arch_db,
        )
        self.assertFalse(list_arch.xpath("//field[@name='preparation_status']"))
        validation_nodes = list_arch.xpath("//field[@name='validation_status']")
        self.assertEqual(len(validation_nodes), 1)
        self.assertEqual(validation_nodes[0].get("column_invisible"), "True")

        form_arch = etree.fromstring(
            self.env.ref(
                "rebuild_account_migration.view_rebuild_account_declaration_form",
            ).arch_db,
        )
        for field_name in (
            "applicability",
            "preparation_status",
            "validation_status",
            "review_status",
            "filing_status",
        ):
            nodes = form_arch.xpath(
                f"//sheet//field[@name='{field_name}'][not(ancestor::list)]",
            )
            self.assertTrue(nodes, field_name)
            self.assertTrue(all(node.get("widget") == "badge" for node in nodes))
            self.assertTrue(all(node.get("readonly") == "1" for node in nodes))
            self.assertTrue(
                all(
                    any(key.startswith("decoration-") for key in node.attrib)
                    for node in nodes
                ),
                field_name,
            )
        for field_name in ("acceptance_status", "payment_status"):
            node = form_arch.xpath(
                f"//sheet//field[@name='{field_name}']",
            )[0]
            self.assertEqual(node.get("widget"), "badges_selection")
        basis_sections = form_arch.xpath(
            "//section[contains(@class, 'o_usl_declaration_basis')]",
        )
        self.assertEqual(len(basis_sections), 1)
        basis = basis_sections[0]
        self.assertEqual(
            len(
                basis.xpath(
                    ".//*[contains(concat(' ', normalize-space(@class), ' '), "
                    "' o_usl_declaration_basis_card ')]",
                ),
            ),
            2,
        )
        self.assertTrue(basis.xpath(".//field[@name='applicability_reason']"))
        self.assertTrue(basis.xpath(".//field[@name='deadline_basis']"))

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

    def test_versioned_rules_deadlines_and_idempotent_profile_sync(self):
        company = self._company()
        company.company_registry = "98398295000021"
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

        self.assertEqual(second.ids, first.ids)
        self.assertEqual(
            Declaration.search_count([("company_id", "=", company.id)]),
            len(first),
        )
        self.assertFalse(first.filtered(lambda item: item.rule_id.code in {"FR_2033", "FR_2069_RCI", "FR_RCM_2777"}))
        self.assertTrue({"2025", "2026", "2027"}.issubset(set(first.mapped("rule_version"))))
        result_dossiers = first.filtered(
            lambda item: item.rule_id.code == "FR_2065",
        )
        self.assertTrue(result_dossiers)
        self.assertEqual(set(result_dossiers.mapped("form_code")), {"2065-SD result dossier"})
        self.assertTrue(all(
            "2033 A-G-SD" in (item.rule_id.supporting_form_codes or "")
            for item in result_dossiers
        ))

        instalments = first.filtered(lambda item: item.rule_id.code == "FR_3514")
        refund_codes = {
            "USL_CA12_REFUND_ACCEPTED",
            "USL_CA12_LATER_REFUND",
            "USL_CA12_REMAINING_CREDIT",
        }
        self.assertEqual(len(instalments), 5)
        self.assertFalse(instalments.field_line_ids.filtered(lambda line: line.field_code in refund_codes))
        self.assertEqual(set(instalments.mapped("payment_status")), {"not_assessed"})
        self.assertTrue(all(instalments.field_line_ids.filtered(
            lambda line: line.field_code == "VAT_3514_PORTAL_AMOUNT",
        ).mapped("is_unresolved")))

        ca12 = first.filtered(lambda item: item.rule_id.code == "FR_3517_S")
        self.assertEqual(len(ca12), 4)
        self.assertEqual(len(ca12.field_line_ids.filtered(lambda line: line.field_code in refund_codes)), 3)
        self.assertEqual(
            set(ca12.field_line_ids.filtered(
                lambda line: line.field_code == "USL_CA12_942_LEDGER_CLASSIFICATION",
            ).mapped("validation_status")),
            {"matched"},
        )

        first_year_is = first.filtered(
            lambda item: item.rule_id.code == "FR_2571" and item.fiscalyear_end == date(2025, 9, 30),
        )
        self.assertFalse(first_year_is)
        later_is_instalments = first.filtered(
            lambda item: item.rule_id.code == "FR_2571",
        )
        self.assertTrue(later_is_instalments)
        self.assertEqual(set(later_is_instalments.mapped("applicability")), {"conditional"})
        self.assertEqual(set(later_is_instalments.mapped("validation_status")), {"warning"})
        self.assertEqual(set(later_is_instalments.mapped("preparation_status")), {"missing_data"})
        self.assertFalse(later_is_instalments.filtered("is_overdue"))

    def test_long_first_year_and_vat_transition_schedule(self):
        company = self._company("USL MEDIA schedule")
        company.write({
            "rebuild_first_fiscalyear_start": "2026-06-01",
            "rebuild_first_fiscalyear_end": "2027-09-30",
        })
        Declaration = self.env["rebuild.account.declaration"]

        with patch.object(fields.Date, "context_today", return_value=date(2026, 8, 29)):
            declarations = Declaration.sync_for_company(company)

        self.assertFalse(declarations.filtered(
            lambda item: item.rule_id.code == "FR_2571"
            and item.fiscalyear_end == date(2027, 9, 30),
        ))
        result = declarations.filtered(
            lambda item: item.rule_id.code == "FR_2065"
            and item.fiscalyear_end == date(2027, 9, 30),
        )
        self.assertEqual(result.deadline_date, date(2028, 1, 15))
        self.assertIn("2033 A-G-SD", result.rule_id.supporting_form_codes)

        ca12 = declarations.filtered(lambda item: item.rule_id.code == "FR_3517_S")
        self.assertEqual(
            set((item.period_start, item.period_end, item.deadline_date) for item in ca12),
            {
                (date(2026, 6, 1), date(2026, 12, 31), date(2027, 5, 4)),
                (date(2027, 1, 1), date(2027, 9, 30), date(2027, 12, 31)),
            },
        )
        vat_instalments = declarations.filtered(lambda item: item.rule_id.code == "FR_3514")
        self.assertEqual(
            set(
                (item.period_start, item.period_end, item.deadline_date)
                for item in vat_instalments
            ),
            {
                (date(2026, 6, 1), date(2026, 6, 30), date(2026, 7, 24)),
                (date(2026, 7, 1), date(2026, 12, 31), date(2026, 12, 24)),
                (date(2027, 1, 1), date(2027, 6, 30), date(2027, 7, 24)),
            },
        )
        self.assertTrue(declarations.filtered(
            lambda item: item.rule_id.code == "FR_CA3"
            and item.period_start == date(2027, 10, 1),
        ))
        self.assertTrue(declarations.filtered(
            lambda item: item.rule_id.code == "FR_CFE_1447_C"
            and item.deadline_date == date(2026, 12, 31),
        ))
        self.assertTrue(declarations.filtered(
            lambda item: item.rule_id.code == "FR_CFE_BALANCE"
            and item.deadline_date == date(2027, 12, 15),
        ))

    def test_transaction_registration_and_threshold_rules_create_only_real_periods(self):
        company = self._company("Triggered declaration schedule")
        company.rebuild_oss_registered = True

        rcm = self._account(company, "457991", "Dividend payable", "liability_current")
        das2_expense = self._account(company, "622991", "Professional fees", "expense")
        equity = self._account(company, "101991", "Trigger balancing equity", "equity")
        cash = self._account(company, "512991", "Trigger bank", "asset_cash")
        revenue = self._account(company, "706991", "Trigger revenue", "income")
        journal = self.env["account.journal"].with_company(company).create({
            "name": "Declaration trigger journal",
            "code": "DTRG",
            "type": "general",
            "company_id": company.id,
        })
        bank_journal = self.env["account.journal"].with_company(company).create({
            "name": "Declaration payment journal",
            "code": "DPAY",
            "type": "bank",
            "company_id": company.id,
        })
        beneficiary = self.env["res.partner"].create({
            "name": "DAS2 threshold beneficiary",
        })
        revenue_move = self.env["account.move"].with_company(company).create({
            "date": "2026-06-30",
            "journal_id": journal.id,
            "line_ids": [
                Command.create({
                    "name": "CVAE turnover",
                    "account_id": cash.id,
                    "debit": 160_000.0,
                }),
                Command.create({
                    "name": "CVAE turnover",
                    "account_id": revenue.id,
                    "credit": 160_000.0,
                }),
            ],
        })
        revenue_move.action_post()
        prior_revenue_move = self.env["account.move"].with_company(company).create({
            "date": "2025-06-30",
            "journal_id": journal.id,
            "line_ids": [
                Command.create({
                    "name": "Prior CVAE turnover",
                    "account_id": cash.id,
                    "debit": 510_000.0,
                }),
                Command.create({
                    "name": "Prior CVAE turnover",
                    "account_id": revenue.id,
                    "credit": 510_000.0,
                }),
            ],
        })
        prior_revenue_move.action_post()
        dividend_move = self.env["account.move"].with_company(company).create({
            "date": "2026-08-10",
            "journal_id": journal.id,
            "line_ids": [
                Command.create({
                    "name": "RCM event",
                    "account_id": equity.id,
                    "debit": 100.0,
                }),
                Command.create({
                    "name": "RCM event",
                    "account_id": rcm.id,
                    "credit": 100.0,
                }),
            ],
        })
        dividend_move.action_post()
        das2_move = self.env["account.move"].with_company(company).create({
            "date": "2025-03-10",
            "journal_id": bank_journal.id,
            "line_ids": [
                Command.create({
                    "name": "Professional fees",
                    "account_id": das2_expense.id,
                    "partner_id": beneficiary.id,
                    "debit": 2_501.0,
                }),
                Command.create({
                    "name": "Professional fees",
                    "account_id": equity.id,
                    "partner_id": beneficiary.id,
                    "credit": 2_501.0,
                }),
            ],
        })
        das2_move.action_post()
        self.env["rebuild.account.external.report.value"].create({
            "name": "Reviewed prior CVAE liability",
            "company_id": company.id,
            "currency_id": company.currency_id.id,
            "period_key": "Calendar year 2025",
            "form_code": "1329-CVAE-PRIOR",
            "field_code": "PRIOR_LIABILITY",
            "source_key": "unit-cvae-prior-2025",
            "amount": 2_000.0,
            "review_status": "accepted",
        })

        Declaration = self.env["rebuild.account.declaration"]

        def eu_b2b_signal(_records, _company, period_start, period_end):
            return (
                period_start == date(2026, 8, 1)
                and period_end == date(2026, 8, 31)
            )

        with (
            patch.object(fields.Date, "context_today", return_value=date(2026, 8, 29)),
            patch.object(
                type(Declaration),
                "_has_eu_b2b_service_signal",
                autospec=True,
                side_effect=eu_b2b_signal,
            ),
        ):
            declarations = Declaration.sync_for_company(company)

        rcm_declaration = declarations.filtered(
            lambda item: item.rule_id.code == "FR_RCM_2777",
        )
        self.assertEqual(
            (rcm_declaration.period_start, rcm_declaration.period_end, rcm_declaration.deadline_date),
            (date(2026, 8, 1), date(2026, 8, 31), date(2026, 9, 15)),
        )
        self.assertNotIn("FY ending", rcm_declaration.name)
        self.assertTrue(declarations.filtered(
            lambda item: item.rule_id.code == "FR_IFU_2561"
            and item.period_start == date(2026, 1, 1),
        ))
        self.assertTrue(declarations.filtered(
            lambda item: item.rule_id.code == "FR_DES"
            and item.period_start == date(2026, 8, 1),
        ))
        self.assertTrue(declarations.filtered(
            lambda item: item.rule_id.code == "FR_OSS"
            and item.period_start == date(2026, 4, 1)
            and item.deadline_date == date(2026, 7, 31),
        ))
        self.assertTrue(declarations.filtered(
            lambda item: item.rule_id.code == "FR_DAS2"
            and item.period_start == date(2025, 1, 1)
            and item.deadline_date == date(2026, 12, 30),
        ))
        self.assertTrue(declarations.filtered(
            lambda item: item.rule_id.code == "FR_CVAE_1330"
            and item.fiscalyear_end == date(2026, 9, 30),
        ))
        self.assertEqual(
            set(declarations.filtered(
                lambda item: item.rule_id.code == "FR_CVAE_1329_AC"
                and item.period_start == date(2026, 1, 1),
            ).mapped("deadline_date")),
            {date(2026, 6, 15), date(2026, 9, 15)},
        )
        das2_rule = self.env.ref(
            "rebuild_account_migration.declaration_rule_das2",
        )
        cvae_instalment_rule = self.env.ref(
            "rebuild_account_migration.declaration_rule_cvae_1329",
        )
        self.assertEqual(das2_rule.threshold_amount, 2400.0)
        self.assertEqual(cvae_instalment_rule.threshold_amount, 500000.0)
        self.assertEqual(cvae_instalment_rule.secondary_threshold_amount, 1500.0)

    def test_das2_uses_payments_not_unpaid_expense_accruals(self):
        company = self._company("DAS2 payment-basis schedule")
        fees = self._account(company, "622992", "Unpaid fees", "expense")
        equity = self._account(company, "101992", "Accrual counterpart", "equity")
        journal = self.env["account.journal"].with_company(company).create({
            "name": "DAS2 accrual journal",
            "code": "DACR",
            "type": "general",
            "company_id": company.id,
        })
        beneficiary = self.env["res.partner"].create({
            "name": "Unpaid DAS2 beneficiary",
        })
        accrual = self.env["account.move"].with_company(company).create({
            "date": "2025-07-10",
            "journal_id": journal.id,
            "line_ids": [
                Command.create({
                    "name": "Unpaid professional fees",
                    "account_id": fees.id,
                    "partner_id": beneficiary.id,
                    "debit": 3_000.0,
                }),
                Command.create({
                    "name": "Unpaid professional fees",
                    "account_id": equity.id,
                    "partner_id": beneficiary.id,
                    "credit": 3_000.0,
                }),
            ],
        })
        accrual.action_post()

        Declaration = self.env["rebuild.account.declaration"]
        self.assertFalse(Declaration._has_das2_signal(
            company,
            date(2025, 1, 1),
            date(2025, 12, 31),
        ))
        with patch.object(fields.Date, "context_today", return_value=date(2026, 8, 29)):
            declarations = Declaration.sync_for_company(company)
        self.assertFalse(declarations.filtered(
            lambda item: item.rule_id.code == "FR_DAS2"
            and item.period_start == date(2025, 1, 1),
        ))

    def test_sync_retires_obsolete_open_rows_without_rewriting_filed_evidence(self):
        company = self._company("Declaration retirement audit")
        retired_rule = self.env.ref(
            "rebuild_account_migration.declaration_rule_2033_2026",
        )
        base_values = {
            "name": "Obsolete independent 2033 obligation",
            "company_id": company.id,
            "rule_id": retired_rule.id,
            "period_start": date(2025, 10, 1),
            "period_end": date(2026, 9, 30),
            "fiscalyear_start": date(2025, 10, 1),
            "fiscalyear_end": date(2026, 9, 30),
            "deadline_date": date(2027, 1, 15),
            "deadline_basis": "Legacy fiscal-year-only schedule.",
            "applicability_reason": "Legacy generated row.",
        }
        open_row = self.env["rebuild.account.declaration"].create(base_values)
        filed_row = self.env["rebuild.account.declaration"].create({
            **base_values,
            "name": "Filed legacy 2033 evidence",
            "instalment_number": 1,
            "status": "filed",
            "filing_status": "filed",
            "external_filing_reference": "UNIT-FILED-2033",
        })

        with patch.object(fields.Date, "context_today", return_value=date(2026, 8, 29)):
            self.env["rebuild.account.declaration"].sync_for_company(company)

        self.assertEqual(open_row.status, "not_applicable")
        self.assertEqual(open_row.applicability, "not_applicable")
        self.assertIn("retained for audit traceability", open_row.applicability_reason)
        self.assertEqual(filed_row.status, "filed")
        self.assertEqual(filed_row.external_filing_reference, "UNIT-FILED-2033")

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
        media_company = self._company("USL MEDIA reviewer scope")
        media_declaration = self._declaration(media_company)
        foreign_company = self._company("Outside reviewer scope")
        foreign_declaration = self._declaration(foreign_company)
        (declaration | media_declaration | foreign_declaration).write({
            "status": "accountant_review",
            "review_status": "accountant_requested",
        })
        reviewer = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Declaration Reviewer",
            "login": "declaration.reviewer@example.invalid",
            "email": "declaration.reviewer@example.invalid",
            "company_id": company.id,
            "company_ids": [Command.set([company.id, media_company.id])],
            "group_ids": [Command.set([self.reviewer_group.id])],
        })

        reviewer_context = {"allowed_company_ids": reviewer.company_ids.ids}
        reviewer_declaration = declaration.with_user(reviewer).with_context(
            **reviewer_context,
        )
        reviewer_closing = closing.with_user(reviewer).with_context(
            **reviewer_context,
        )
        self.assertEqual(
            reviewer_declaration.read(["name"])[0]["name"],
            declaration.name,
        )
        self.assertEqual(
            reviewer_closing.read(["name"])[0]["name"],
            closing.name,
        )
        with self.assertRaises(AccessError):
            reviewer_declaration.write({"status": "ready_to_file"})
        with self.assertRaises(AccessError):
            reviewer_closing.write({"state": "ready"})

        decision_model = self.env["rebuild.account.assurance.decision"]
        reviewer_decisions = decision_model.with_user(reviewer).with_context(
            **reviewer_context,
        )
        self.assertTrue(reviewer_decisions.has_access("read"))
        self.assertTrue(reviewer_decisions.has_access("write"))
        self.assertTrue(reviewer_decisions.has_access("create"))
        reviewer_decision = reviewer_decisions.create({
            "gate": "declaration_review",
            "conclusion": "accepted_with_difference",
            "required_authority": "accountant",
            "company_id": company.id,
            "declaration_id": declaration.id,
            "decision_summary": "Reviewed the declaration and documented the remaining portal confirmation.",
            "evidence_summary": "Declaration schedule and ledger-derived field review.",
        })
        reviewer_decision.action_record()
        self.assertEqual(reviewer_decision.reviewer_user_id, reviewer)
        self.assertEqual(declaration.review_status, "accepted_with_difference")
        media_decision = reviewer_decisions.create({
            "gate": "declaration_review",
            "conclusion": "requires_change",
            "required_authority": "accountant",
            "company_id": media_company.id,
            "declaration_id": media_declaration.id,
            "decision_summary": "USL MEDIA declaration requires a corrected supporting fact.",
        })
        media_decision.action_record()
        self.assertEqual(media_declaration.review_status, "rejected")
        with self.assertRaises(AccessError):
            reviewer_decisions.create({
                "gate": "closing_review",
                "conclusion": "accepted_with_difference",
                "required_authority": "accountant",
                "company_id": company.id,
                "closing_period_id": closing.id,
                "decision_summary": "A scoped declaration reviewer cannot approve closing workspaces.",
            })
        with self.assertRaises(AccessError):
            reviewer_decision.action_supersede()
        with self.assertRaises(AccessError):
            reviewer_decisions.create({
                "gate": "declaration_review",
                "conclusion": "requires_change",
                "required_authority": "accountant",
                "company_id": foreign_company.id,
                "declaration_id": foreign_declaration.id,
                "decision_summary": "Cross-company action must remain impossible.",
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
            "raw": package_payload,
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
        self.assertEqual(bytes(snapshot.payload), package_payload)
        self.assertEqual(
            snapshot.with_user(reviewer).with_context(
                **reviewer_context,
            ).read(["sha256"])[0]["sha256"],
            snapshot.sha256,
        )
        reviewer_snapshot = snapshot.with_user(reviewer).with_context(
            **reviewer_context,
        )
        self.assertFalse(reviewer_snapshot.has_access("write"))
        with self.assertRaisesRegex(UserError, "immutable"):
            reviewer_snapshot.write({"name": "Changed"})
        with self.assertRaisesRegex(UserError, "immutable"):
            snapshot.write({"name": "Changed"})
        with self.assertRaisesRegex(UserError, "locked"):
            closing.write({"package_reference": "Changed after acceptance"})
        with self.assertRaisesRegex(UserError, "locked"):
            package_attachment.write({
                "raw": b"changed package",
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
        closing_decision = self.env["rebuild.account.assurance.decision"].create({
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
        xlsx_payload = bytes(wizard.export_file)
        self.assertTrue(xlsx_payload.startswith(b"PK"))
        self.assertGreater(len(xlsx_payload), 8_000)
        with zipfile.ZipFile(io.BytesIO(xlsx_payload)) as workbook_archive:
            workbook_xml = workbook_archive.read("xl/workbook.xml")
            shared_strings = workbook_archive.read("xl/sharedStrings.xml")
        self.assertIn(b'Metadata', workbook_xml)
        self.assertIn(b'Report', workbook_xml)
        self.assertIn(b'Audit Data', workbook_xml)
        self.assertIn(b'Dossier de revue de cl\xc3\xb4ture', shared_strings)
        self.assertIn(b'Lock dates', shared_strings)

        wizard.export_format = "pdf"
        renderer = self.env["usl.document.renderer"]
        with (
            patch.object(
                type(company),
                "_usl_document_renderer_company_payload",
                return_value=(COMPANY_PAYLOAD, []),
            ),
            patch.object(type(renderer), "render", return_value=RENDER_RESULT),
        ):
            wizard.action_generate_export()
        pdf_payload = bytes(wizard.export_file)
        self.assertTrue(pdf_payload.startswith(b"%PDF"))
        self.assertGreater(len(pdf_payload), 10_000)
        pdf_metadata = json.loads(wizard.export_metadata)
        self.assertEqual(
            pdf_metadata["document_render"]["template_revision"],
            "reviewed-template",
        )
        export_attachment = self.env["ir.attachment"].search(
            [
                ("res_model", "=", wizard._name),
                ("res_id", "=", wizard.id),
                ("res_field", "=", "export_file"),
            ],
            limit=1,
        )
        self.assertEqual(export_attachment.usl_document_payload_sha256, "c" * 64)
        self.assertEqual(len(closing.package_attachment_ids), 2)

        closing_decision.action_record()
        self.assertEqual(closing.review_status, "accepted")
        self.assertEqual(closing.state, "ready")
        self.assertEqual(closing.snapshot_count, 2)
        self.assertEqual(
            set(closing.snapshot_ids.mapped("sha256")),
            {
                hashlib.sha256(
                    bytes(attachment.raw),
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
        # This test deliberately posts a wrong-way income balance to verify
        # the downstream Hygiene detector rather than the posting guard.
        income.rebuild_entry_direction_policy = "none"
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
        hygiene = self.env["rebuild.account.overview"].search([
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
        self.assertIn("1 supplier document", evidence_issue.evidence)
        self.assertNotIn(company.currency_id.name, evidence_issue.evidence)
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

        evidence_definition.write({
            "description": "Checks supplier evidence after review.",
            "expected_resolution": "Every supplier document is supported.",
        })
        self.assertEqual(
            evidence_definition.business_purpose,
            "Checks supplier evidence after review.",
        )
        self.assertEqual(
            evidence_definition.expected_outcome,
            "Every supplier document is supported.",
        )

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

    def test_opening_hygiene_refreshes_for_manager_only(self):
        company = self._company("Automatic Hygiene Refresh Company", profile=False)
        overview = self.env["rebuild.account.overview"].with_company(company).search(
            [("company_id", "in", [company.id])],
            limit=1,
        )
        self.assertTrue(overview)

        refreshed_action = {
            "type": "ir.actions.act_window",
            "res_model": "rebuild.account.hygiene.issue",
        }
        with patch.object(
            type(overview),
            "action_refresh_hygiene",
            autospec=True,
            return_value=refreshed_action,
        ) as refresh:
            self.assertEqual(
                overview.action_open_hygiene_issues(),
                refreshed_action,
            )
        refresh.assert_called_once_with(overview)

        reviewer = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Automatic Hygiene Reviewer",
            "login": "automatic.hygiene.reviewer@example.invalid",
            "email": "automatic.hygiene.reviewer@example.invalid",
            "company_id": company.id,
            "company_ids": [Command.set([company.id])],
            "group_ids": [Command.set([self.reviewer_group.id])],
        })
        reviewer_overview = overview.with_user(reviewer).with_context(
            allowed_company_ids=reviewer.company_ids.ids,
        )
        with patch.object(
            type(reviewer_overview),
            "action_refresh_hygiene",
            autospec=True,
        ) as refresh:
            reviewer_action = reviewer_overview.action_open_hygiene_issues()
        refresh.assert_not_called()
        self.assertEqual(
            reviewer_action["res_model"],
            "rebuild.account.hygiene.issue",
        )
        self.assertEqual(
            reviewer_action["domain"],
            [("company_id", "in", [company.id])],
        )

        client_action = self.env.ref(
            "rebuild_account_migration.action_open_current_company_hygiene",
        )
        self.assertEqual(
            client_action.tag,
            "rebuild_accounting_hygiene",
        )
        menu = self.env.ref(
            "rebuild_account_migration.menu_rebuild_account_review_issues_priority",
        )
        self.assertEqual(
            menu.action,
            client_action,
        )

    def test_hygiene_dismissal_is_scoped_to_material_evidence(self):
        company = self._company("Scoped Hygiene Dismissal Company")
        expense_account = self._account(
            company,
            "606200",
            "Dismissal supplies",
            "expense",
        )
        payable_account = self._account(
            company,
            "401200",
            "Dismissal suppliers",
            "liability_payable",
            True,
        )
        journal = self.env["account.journal"].with_company(company).create({
            "name": "Dismissal Purchases",
            "code": "DISM",
            "type": "purchase",
            "company_id": company.id,
            "default_account_id": expense_account.id,
        })
        partner = self.env["res.partner"].with_company(company).create({
            "name": "Dismissal Supplier",
            "company_id": company.id,
            "property_account_payable_id": payable_account.id,
        })

        def create_bill(price_unit):
            return self.env["account.move"].with_company(company).create({
                "move_type": "in_invoice",
                "company_id": company.id,
                "journal_id": journal.id,
                "partner_id": partner.id,
                "invoice_date": fields.Date.context_today(self.env.user),
                "invoice_line_ids": [
                    Command.create({
                        "name": "Office supplies",
                        "account_id": expense_account.id,
                        "quantity": 1,
                        "price_unit": price_unit,
                    }),
                ],
            })

        first_bill = create_bill(120.0)
        Issue = self.env["rebuild.account.hygiene.issue"].with_company(company)
        Issue.sync_for_company(company)
        issue = Issue.search([
            ("company_id", "=", company.id),
            ("control_code", "=", "hygiene_vendor_evidence"),
        ])
        self.assertEqual(len(issue), 1)
        original_fingerprint = issue.evidence_fingerprint
        self.assertTrue(original_fingerprint)
        self.assertTrue(issue.definition_id.enabled)

        issue.write({
            "status": "dismissed",
            "dismissed_at": fields.Datetime.now(),
            "dismissed_by_id": self.env.user.id,
            "evidence_fingerprint": False,
        })
        Issue.sync_for_company(company)
        self.assertEqual(issue.status, "dismissed")
        self.assertEqual(issue.evidence_fingerprint, original_fingerprint)
        self.assertEqual(len(issue.dismissal_ids), 1)
        self.assertEqual(
            issue.dismissal_ids.evidence_fingerprint,
            original_fingerprint,
        )
        issue.dismissal_ids.unlink()
        issue.write({
            "status": "open",
            "dismissed_at": False,
            "dismissed_by_id": False,
            "evidence_fingerprint": False,
        })

        notification = issue.action_dismiss()
        self.assertEqual(issue.status, "dismissed")
        self.assertEqual(len(issue.dismissal_ids), 1)
        self.assertFalse(issue.dismissal_ids.superseded_at)
        self.assertFalse(issue.dismissal_ids.evidence_fingerprint)
        self.assertEqual(issue.dismissal_ids.related_record_count, 1)
        self.assertEqual(notification["tag"], "display_notification")
        self.assertIn(
            "control remains active",
            notification["params"]["message"],
        )

        Issue.sync_for_company(company)
        self.assertEqual(issue.status, "dismissed")
        self.assertEqual(issue.evidence_fingerprint, original_fingerprint)
        self.assertEqual(
            issue.dismissal_ids.evidence_fingerprint,
            original_fingerprint,
        )
        self.assertEqual(len(issue.dismissal_ids), 1)

        second_bill = create_bill(80.0)
        Issue.sync_for_company(company)
        self.assertEqual(issue.status, "open")
        self.assertNotEqual(issue.evidence_fingerprint, original_fingerprint)
        self.assertTrue(issue.dismissal_ids.superseded_at)
        self.assertEqual(
            set(json.loads(issue.target_res_ids_json)),
            {first_bill.id, second_bill.id},
        )

        reopened_fingerprint = issue.evidence_fingerprint
        Issue.sync_for_company(company)
        self.assertEqual(issue.status, "open")
        self.assertEqual(issue.evidence_fingerprint, reopened_fingerprint)
        self.assertEqual(
            Issue.search_count([
                ("company_id", "=", company.id),
                ("issue_key", "=", issue.issue_key),
            ]),
            1,
        )

        reviewer = self.env["res.users"].with_context(
            no_reset_password=True,
        ).create({
            "name": "Hygiene Dismissal Reviewer",
            "login": "hygiene.dismissal.reviewer@example.invalid",
            "email": "hygiene.dismissal.reviewer@example.invalid",
            "company_id": company.id,
            "company_ids": [Command.set([company.id])],
            "group_ids": [Command.set([self.reviewer_group.id])],
        })
        with self.assertRaises(AccessError):
            issue.with_user(reviewer).action_dismiss()

        first_bill.button_cancel()
        second_bill.button_cancel()
        Issue.sync_for_company(company)
        self.assertEqual(issue.status, "resolved")
        self.assertTrue(issue.resolved_at)
        self.assertEqual(len(issue.dismissal_ids), 1)

    def test_hygiene_dismissal_is_company_scoped(self):
        first_company = self._company("First Dismissal Company")
        second_company = self._company("Second Dismissal Company")
        Issue = self.env["rebuild.account.hygiene.issue"]
        Definition = self.env[
            "rebuild.account.closing.control.definition"
        ]
        first_definition = Definition._ensure_for_company(
            first_company,
        ).filtered(
            lambda definition: (
                definition.code == "hygiene_vendor_evidence"
            ),
        )
        second_definition = Definition._ensure_for_company(
            second_company,
        ).filtered(
            lambda definition: (
                definition.code == "hygiene_vendor_evidence"
            ),
        )
        common_values = {
            "issue_key": "unit:company-scope",
            "control_code": "hygiene_vendor_evidence",
            "issue_type": "evidence",
            "severity": "2_warning",
            "title": "Company-scoped issue",
            "description": "Company-scoped evidence.",
            "why_it_matters": "Company boundaries matter.",
            "recommended_action": "Review this company only.",
            "accounting_consequence": "None outside this company.",
            "evidence": "One unit-test record.",
            "target_model": "res.company",
            "target_res_ids_json": "[]",
            "source_label": "Unit test",
            "evidence_fingerprint": "company-scoped-fingerprint",
        }
        first_issue = Issue.create({
            **common_values,
            "company_id": first_company.id,
            "definition_id": first_definition.id,
            "target_res_id": first_company.id,
        })
        second_issue = Issue.create({
            **common_values,
            "company_id": second_company.id,
            "definition_id": second_definition.id,
            "target_res_id": second_company.id,
        })

        first_issue.action_dismiss()

        self.assertEqual(first_issue.status, "dismissed")
        self.assertEqual(second_issue.status, "open")
        self.assertEqual(first_issue.dismissal_ids.company_id, first_company)
        self.assertFalse(second_issue.dismissal_ids)


@tagged("post_install", "-at_install", "rebuild_account_migration_unit")
class TestMultiCompanyAccountingReports(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.euro = cls.env.ref("base.EUR")
        cls.first_company = cls.env["res.company"].create({
            "name": "Combined Report Alpha",
            "currency_id": cls.euro.id,
        })
        cls.second_company = cls.env["res.company"].create({
            "name": "Combined Report Beta",
            "currency_id": cls.euro.id,
        })
        cls.allowed_env = cls.env(context={
            **cls.env.context,
            "allowed_company_ids": [
                cls.first_company.id,
                cls.second_company.id,
            ],
        })

    def _wizard(self, **values):
        return self.allowed_env[
            "rebuild.account.report.export.wizard"
        ].with_company(self.first_company).create({
            "report_type": "trial_balance",
            "company_id": self.first_company.id,
            "company_ids": [Command.set([
                self.first_company.id,
                self.second_company.id,
            ])],
            "date_from": "2026-01-01",
            "date_to": "2026-12-31",
            **values,
        })

    def _post_revenue(self, company, amount):
        cash = self.env["account.account"].with_company(company).create({
            "code": "512991",
            "name": "Combined report bank",
            "account_type": "asset_cash",
            "company_ids": [Command.set([company.id])],
        })
        revenue = self.env["account.account"].with_company(company).create({
            "code": "706991",
            "name": "Combined report revenue",
            "account_type": "income",
            "company_ids": [Command.set([company.id])],
        })
        journal = self.env["account.journal"].with_company(company).create({
            "name": "Combined report journal",
            "code": "MCR",
            "type": "general",
            "company_id": company.id,
        })
        move = self.env["account.move"].with_company(company).create({
            "date": "2026-06-30",
            "journal_id": journal.id,
            "line_ids": [
                Command.create({
                    "name": "Combined revenue",
                    "account_id": cash.id,
                    "debit": amount,
                    "credit": 0,
                }),
                Command.create({
                    "name": "Combined revenue",
                    "account_id": revenue.id,
                    "debit": 0,
                    "credit": amount,
                }),
            ],
        })
        move.action_post()

    def test_same_currency_summary_rows_are_aggregated_with_contributions(self):
        wizard = self._wizard()
        rows = wizard._aggregate_company_rows([
            {
                "account_code": "512000",
                "account_type": "asset_cash",
                "account_name": "Bank",
                "debit": "100.00",
                "credit": "10.00",
                "closing_balance": "90.00",
                "move_line_count": "2",
                "report_company_id": self.first_company.id,
                "report_company_name": self.first_company.name,
                "report_currency_id": self.euro.id,
                "report_currency": "EUR",
            },
            {
                "account_code": "512000",
                "account_type": "asset_cash",
                "account_name": "Bank",
                "debit": "50.00",
                "credit": "5.00",
                "closing_balance": "45.00",
                "move_line_count": "1",
                "report_company_id": self.second_company.id,
                "report_company_name": self.second_company.name,
                "report_currency_id": self.euro.id,
                "report_currency": "EUR",
            },
        ])

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["debit"], "150.00")
        self.assertEqual(rows[0]["credit"], "15.00")
        self.assertEqual(rows[0]["closing_balance"], "135.00")
        self.assertEqual(rows[0]["move_line_count"], "3")
        self.assertEqual(
            rows[0]["report_company_ids"],
            [self.first_company.id, self.second_company.id],
        )
        self.assertEqual(len(rows[0]["company_contributions"]), 2)
        domain = wizard._preview_journal_item_domain(rows[0])
        self.assertIn(
            (
                "company_id",
                "in",
                [self.first_company.id, self.second_company.id],
            ),
            domain,
        )

    def test_trial_balance_combines_real_company_ledgers(self):
        self._post_revenue(self.first_company, 100)
        self._post_revenue(self.second_company, 25)
        rows = self._wizard()._raw_report_rows(
            fields.Date.from_string("2026-01-01"),
            fields.Date.from_string("2026-12-31"),
        )

        cash = next(row for row in rows if row["account_code"] == "512991")
        revenue = next(row for row in rows if row["account_code"] == "706991")
        self.assertEqual(cash["closing_balance"], "125.00")
        self.assertEqual(revenue["closing_balance"], "-125.00")
        self.assertEqual(len(cash["company_contributions"]), 2)

        individual_balances = {}
        for company in (self.first_company, self.second_company):
            individual = self._wizard(
                company_id=company.id,
                company_ids=[Command.set([company.id])],
            )._raw_report_rows(
                fields.Date.from_string("2026-01-01"),
                fields.Date.from_string("2026-12-31"),
            )
            individual_balances[company.id] = next(
                row for row in individual if row["account_code"] == "512991"
            )["closing_balance"]
        self.assertEqual(
            Decimal(cash["closing_balance"]),
            sum(map(Decimal, individual_balances.values())),
        )

    def test_combined_xlsx_and_pdf_preserve_company_scope(self):
        self._post_revenue(self.first_company, 100)
        self._post_revenue(self.second_company, 25)
        wizard = self._wizard(export_format="xlsx")

        wizard.action_generate_export()
        xlsx_payload = bytes(wizard.export_file)
        self.assertTrue(xlsx_payload.startswith(b"PK"))
        with zipfile.ZipFile(io.BytesIO(xlsx_payload)) as workbook_archive:
            shared_strings = workbook_archive.read("xl/sharedStrings.xml")
        self.assertIn(self.first_company.name.encode(), shared_strings)
        self.assertIn(self.second_company.name.encode(), shared_strings)
        xlsx_metadata = json.loads(wizard.export_metadata)
        self.assertEqual(
            {company["id"] for company in xlsx_metadata["companies"]},
            {self.first_company.id, self.second_company.id},
        )

        wizard.export_format = "pdf"
        renderer = self.allowed_env["usl.document.renderer"]
        with (
            patch.object(
                type(self.first_company),
                "_usl_document_renderer_company_payload",
                return_value=(COMPANY_PAYLOAD, []),
            ),
            patch.object(type(renderer), "render", return_value=RENDER_RESULT),
        ):
            wizard.action_generate_export()
        self.assertTrue(bytes(wizard.export_file).startswith(b"%PDF"))
        pdf_metadata = json.loads(wizard.export_metadata)
        self.assertEqual(
            {company["id"] for company in pdf_metadata["companies"]},
            {self.first_company.id, self.second_company.id},
        )

    def test_pdf_adapter_uses_the_exact_selected_rows_and_display_rules(self):
        wizard = self._wizard(
            export_format="pdf",
            display_unit="thousands",
            amount_rounding="whole",
        )
        rows = [
            {
                "account_code": "706000",
                "account_name": "Professional services",
                "opening_debit": "0.00",
                "opening_credit": "0.00",
                "debit": "1250.49",
                "credit": "0.00",
                "closing_balance": "1250.49",
                "row_level": 2,
                "hierarchy_kind": "account",
            }
        ]
        renderer = self.allowed_env["usl.document.renderer"]
        with (
            patch.object(
                type(self.first_company),
                "_usl_document_renderer_company_payload",
                return_value=(COMPANY_PAYLOAD, []),
            ),
            patch.object(type(renderer), "render", return_value=RENDER_RESULT) as render,
        ):
            result = wizard._pdf_payload(rows, return_result=True)

        self.assertEqual(result, RENDER_RESULT)
        payload = render.call_args.args[2]
        rendered_row = payload["sections"][0]["rows"][0]
        self.assertEqual(rendered_row["level"], 2)
        self.assertIn("1", rendered_row["values"].values())
        self.assertTrue(
            any("Unité : Milliers d’euros" in item for item in payload["context"])
        )
        self.assertTrue(
            any(
                item.startswith("Écritures comptabilisées au ")
                for item in payload["context"]
            )
        )
        self.assertNotIn("Lignes sans mouvement masquées", payload["context"])
        self.assertEqual(payload["orientation"], "landscape")
        self.assertEqual(payload["layout_variant"], "statement")

    def test_pdf_adapter_names_the_currency_instead_of_generic_units(self):
        wizard = self._wizard(export_format="pdf", display_unit="units")
        renderer = self.allowed_env["usl.document.renderer"]
        with (
            patch.object(
                type(self.first_company),
                "_usl_document_renderer_company_payload",
                return_value=(COMPANY_PAYLOAD, []),
            ),
            patch.object(type(renderer), "render", return_value=RENDER_RESULT) as render,
        ):
            wizard._pdf_payload([], return_result=True)

        payload = render.call_args.args[2]
        self.assertTrue(
            any("Unité : Euros" in item for item in payload["context"])
        )

    def test_statement_pdf_uses_business_labels_not_internal_codes(self):
        renderer = self.allowed_env["usl.document.renderer"]
        cases = (
            (
                "profit_loss",
                {
                    "section": "Produits d’exploitation",
                    "line_code": "CR_SERVICES",
                    "line_name": "Prestations de services",
                    "amount": "1250.49",
                },
                "Prestations de services",
                "Montant (€)",
            ),
            (
                "balance_sheet",
                {
                    "section": "Capitaux propres",
                    "account_code": "106100",
                    "account_name": "Réserve légale",
                    "amount": "100.00",
                },
                "Réserve légale",
                "Solde (€)",
            ),
        )
        for report_type, row, expected_label, expected_column in cases:
            wizard = self._wizard(
                report_type=report_type,
                export_format="pdf",
                date_from="2025-10-01",
                date_to="2026-09-30",
            )
            with (
                patch.object(
                    type(self.first_company),
                    "_usl_document_renderer_company_payload",
                    return_value=(COMPANY_PAYLOAD, []),
                ),
                patch.object(
                    type(renderer),
                    "render",
                    return_value=RENDER_RESULT,
                ) as render,
            ):
                wizard._pdf_payload([row], return_result=True)

            payload = render.call_args.args[2]
            self.assertEqual(payload["columns"][1]["label"], expected_column)
            rendered_row = payload["sections"][0]["rows"][0]
            self.assertEqual(rendered_row["values"]["label"], expected_label)
            self.assertNotIn(row.get("line_code"), rendered_row["values"].values())
            self.assertEqual(payload["reference"], "Exercice 2025–2026")
            self.assertEqual(payload["date"], "01/10/2025 – 30/09/2026")

    def test_pdf_adapter_normalizes_negative_zero(self):
        wizard = self._wizard(
            export_format="pdf",
            amount_rounding="whole",
        )
        rows = [{
            "account_code": "471000",
            "account_name": "Compte d’attente",
            "opening_balance": "-0.004",
            "debit": "0",
            "credit": "0",
            "closing_balance": "-0.004",
        }]
        renderer = self.allowed_env["usl.document.renderer"]
        with (
            patch.object(
                type(self.first_company),
                "_usl_document_renderer_company_payload",
                return_value=(COMPANY_PAYLOAD, []),
            ),
            patch.object(
                type(renderer),
                "render",
                return_value=RENDER_RESULT,
            ) as render,
        ):
            wizard._pdf_payload(rows, return_result=True)

        values = render.call_args.args[2]["sections"][0]["rows"][0]["values"]
        self.assertNotIn("-0", values.values())

    def test_trial_balance_groups_pcg_classes_and_adds_equality_control(self):
        wizard = self._wizard(group_by="section")
        grouped = wizard._group_report_rows([
            {
                "section": "Classe 4 — Comptes de tiers",
                "account_code": "401000",
                "account_name": "Fournisseurs",
                "debit": "100.00",
                "credit": "100.00",
                "closing_balance": "0.00",
            },
        ])
        rows = wizard._append_shared_control_rows(grouped)

        self.assertEqual(rows[0]["label"], "Classe 4 — Comptes de tiers")
        self.assertEqual(rows[0]["debit"], "100.00")
        self.assertEqual(rows[-1]["presentation_role"], "control")
        self.assertEqual(rows[-1]["control_status"], "success")

    def test_journal_hierarchy_groups_by_type_before_journal(self):
        wizard = self._wizard(report_type="journal_report", group_by="journal")
        rows = wizard._group_report_rows([
            {
                "journal_type": "sale",
                "journal_code": "VE",
                "journal_name": "Ventes",
                "debit": "125.00",
                "credit": "125.00",
                "balance": "0.00",
            },
        ])

        self.assertEqual(rows[0]["label"], "Journaux de ventes")
        self.assertEqual(rows[0]["presentation_role"], "section")
        self.assertEqual(rows[1]["label"], "VE — Ventes")
        self.assertEqual(rows[1]["parent_group_key"], rows[0]["group_key"])

    def test_partner_ledger_nests_accounts_and_closing_subtotals(self):
        wizard = self._wizard(report_type="partner_ledger", group_by="partner")
        rows = wizard._group_report_rows([
            {
                "partner_name": "Client Démonstration",
                "account_code": "411000",
                "account_name": "Clients",
                "opening_balance": "20.00",
                "debit": "100.00",
                "credit": "0.00",
                "balance": "100.00",
                "running_balance": "120.00",
            },
        ])

        self.assertEqual(rows[0]["presentation_role"], "section")
        self.assertEqual(rows[1]["presentation_role"], "group")
        self.assertEqual(rows[1]["opening_balance"], "20.00")
        self.assertEqual(rows[-1]["presentation_role"], "subtotal")
        self.assertEqual(rows[-1]["running_balance"], "120.00")

    def test_open_items_separate_receivables_and_payables(self):
        wizard = self._wizard(report_type="open_items", group_by="partner")
        rows = wizard._group_report_rows([
            {
                "account_type": "asset_receivable",
                "partner_name": "Client QA",
                "presented_residual": "90.00",
            },
            {
                "account_type": "liability_payable",
                "partner_name": "Fournisseur QA",
                "presented_residual": "40.00",
            },
        ])

        sections = [
            row["label"]
            for row in rows
            if row.get("presentation_role") == "section"
        ]
        self.assertEqual(sections, ["Clients", "Fournisseurs"])

    def test_balance_sheet_pdf_has_dedicated_sides_and_exact_control(self):
        wizard = self._wizard(report_type="balance_sheet", group_by="section")
        rows = wizard._balance_sheet_hierarchy_rows([
            {
                "statement_key": "bilan_actif",
                "section": "Actif circulant",
                "account_name": "Banque",
                "amount": "100.00",
            },
            {
                "statement_key": "bilan_actif",
                "line_code": "ACTIF_TOTAL",
                "account_name": "Total Actif",
                "amount": "100.00",
                "presentation_role": "total",
            },
            {
                "statement_key": "bilan_passif",
                "section": "Capitaux propres",
                "account_name": "Capital",
                "amount": "100.00",
            },
            {
                "statement_key": "bilan_passif",
                "line_code": "PASSIF_TOTAL",
                "account_name": "Total Passif",
                "amount": "100.00",
                "presentation_role": "total",
            },
        ])
        side_headers = {
            row["statement_key"]: row
            for row in rows
            if row.get("presentation_role") == "section"
        }
        self.assertEqual(side_headers["bilan_actif"]["amount"], "100.00")
        self.assertEqual(side_headers["bilan_passif"]["amount"], "100.00")
        total_rows = {
            row["line_code"]: row
            for row in rows
            if row.get("line_code") in {"ACTIF_TOTAL", "PASSIF_TOTAL"}
        }
        for display_key in ("presentation_role", "is_group", "row_level"):
            self.assertEqual(
                total_rows["ACTIF_TOTAL"][display_key],
                total_rows["PASSIF_TOTAL"][display_key],
            )
        self.assertEqual(
            total_rows["PASSIF_TOTAL"]["presentation_role"],
            "total",
        )
        rows = wizard._append_shared_control_rows(rows)
        renderer = self.allowed_env["usl.document.renderer"]
        with (
            patch.object(
                type(self.first_company),
                "_usl_document_renderer_company_payload",
                return_value=(COMPANY_PAYLOAD, []),
            ),
            patch.object(type(renderer), "render", return_value=RENDER_RESULT) as render,
        ):
            wizard._pdf_payload(rows, return_result=True)

        payload = render.call_args.args[2]
        self.assertEqual([section["title"] for section in payload["sections"]], ["Actif", "Passif"])
        self.assertTrue(payload["sections"][1]["break_before"])
        self.assertEqual(
            [control["label"] for control in payload["controls"]],
            [
                "Total actif",
                "Total passif",
                "Écart actif − passif",
            ],
        )
        self.assertEqual(payload["controls"][-1]["status"], "success")

    def test_balance_sheet_uses_closing_balances(self):
        wizard = self._wizard(report_type="balance_sheet")
        trial_balance = [
            {
                "account_code": "512000",
                "account_name": "Banque",
                "account_type": "asset_cash",
                "balance": "0.00",
                "closing_balance": "100.00",
            },
            {
                "account_code": "101000",
                "account_name": "Capital",
                "account_type": "equity",
                "balance": "0.00",
                "closing_balance": "-100.00",
            },
        ]
        with patch.object(
            type(wizard),
            "_trial_balance_rows",
            return_value=trial_balance,
        ):
            rows = wizard._balance_sheet_rows()

        totals = {
            row.get("line_code"): row.get("amount")
            for row in rows
            if row.get("line_code")
        }
        self.assertEqual(totals["ACTIF_TOTAL"], "100.00")
        self.assertEqual(totals["PASSIF_TOTAL"], "100.00")

    def test_balance_sheet_uses_french_debit_credit_prefix_rules(self):
        wizard = self._wizard(report_type="balance_sheet")
        trial_balance = [
            {
                "account_code": "444000",
                "account_name": "État — impôts sur les bénéfices",
                "account_type": "liability_current",
                "balance": "5670.00",
                "closing_balance": "5670.00",
            },
            {
                "account_code": "512008",
                "account_name": "Banque — compte créditeur",
                "account_type": "asset_cash",
                "balance": "-0.16",
                "closing_balance": "-0.16",
            },
            {
                "account_code": "491000",
                "account_name": "Dépréciation des comptes clients",
                "account_type": "asset_current",
                "balance": "-20.00",
                "closing_balance": "-20.00",
            },
        ]
        with patch.object(
            type(wizard),
            "_trial_balance_rows",
            return_value=trial_balance,
        ):
            rows = wizard._balance_sheet_rows()

        accounts = {
            row["account_code"]: row
            for row in rows
            if row.get("account_code") not in {"", "RESULT"}
        }
        self.assertEqual(accounts["444000"]["statement_key"], "bilan_actif")
        self.assertEqual(accounts["444000"]["amount"], "5670.00")
        self.assertEqual(accounts["444000"]["section"], "Actif circulant")
        self.assertEqual(accounts["512008"]["statement_key"], "bilan_passif")
        self.assertEqual(accounts["512008"]["amount"], "0.16")
        self.assertEqual(accounts["512008"]["section"], "Dettes et passifs")
        self.assertEqual(accounts["491000"]["statement_key"], "bilan_actif")
        self.assertEqual(accounts["491000"]["amount"], "-20.00")

    def test_balance_sheet_matches_online_total_at_2026_08_29(self):
        wizard = self._wizard(report_type="balance_sheet")
        trial_balance = [
            {
                "account_code": "512007",
                "account_name": "Banque — soldes débiteurs",
                "account_type": "asset_cash",
                "balance": "131376.84",
                "closing_balance": "131376.84",
            },
            {
                "account_code": "444000",
                "account_name": "État — impôts sur les bénéfices",
                "account_type": "liability_current",
                "balance": "5670.00",
                "closing_balance": "5670.00",
            },
            {
                "account_code": "512008",
                "account_name": "Banque — compte créditeur",
                "account_type": "asset_cash",
                "balance": "-0.16",
                "closing_balance": "-0.16",
            },
            {
                "account_code": "401100",
                "account_name": "Fournisseurs — solde débiteur",
                "account_type": "liability_payable",
                "balance": "16963.64",
                "closing_balance": "16963.64",
            },
            {
                "account_code": "471000",
                "account_name": "Compte d’attente — solde créditeur",
                "account_type": "asset_current",
                "balance": "-4883.54",
                "closing_balance": "-4883.54",
            },
            {
                "account_code": "101000",
                "account_name": "Capital social et autres passifs",
                "account_type": "equity",
                "balance": "-149126.78",
                "closing_balance": "-149126.78",
            },
        ]
        self.assertEqual(
            sum(
                Decimal(row["closing_balance"])
                for row in trial_balance
            ),
            Decimal("0.00"),
        )
        with patch.object(
            type(wizard),
            "_trial_balance_rows",
            return_value=trial_balance,
        ):
            rows = wizard._balance_sheet_rows()

        totals = {
            row.get("line_code"): row.get("amount")
            for row in rows
            if row.get("line_code") in {"ACTIF_TOTAL", "PASSIF_TOTAL"}
        }
        online_total = "132163.30"
        self.assertEqual(totals["ACTIF_TOTAL"], online_total)
        self.assertEqual(totals["PASSIF_TOTAL"], online_total)
        self.assertEqual(totals["ACTIF_TOTAL"], totals["PASSIF_TOTAL"])

        accounts = {
            row["account_code"]: row
            for row in rows
            if row.get("account_code") not in {"", "RESULT"}
        }
        self.assertEqual(accounts["401100"]["statement_key"], "bilan_passif")
        self.assertEqual(accounts["401100"]["amount"], "-16963.64")
        self.assertEqual(accounts["471000"]["statement_key"], "bilan_actif")
        self.assertEqual(accounts["471000"]["amount"], "-4883.54")

        with patch.object(
            type(wizard),
            "_trial_balance_rows",
            return_value=trial_balance,
        ):
            interactive_rows = wizard._french_annual_rows(
                statement_keys={"bilan_actif", "bilan_passif"},
            )
        interactive_totals = {
            row.get("line_code"): row.get("amount")
            for row in interactive_rows
            if row.get("line_code") in {"ACTIF_TOTAL", "PASSIF_TOTAL"}
        }
        self.assertEqual(interactive_totals["ACTIF_TOTAL"], online_total)
        self.assertEqual(interactive_totals["PASSIF_TOTAL"], online_total)

    def test_asset_register_places_grand_total_after_account_subtotals(self):
        wizard = self._wizard(report_type="fixed_assets", export_format="pdf")
        rows = [
            {
                "label": "215400 — Matériel industriel",
                "account_code": "215400",
                "is_group": True,
                "group_key": "account:215400",
                "row_level": 0,
                "presentation_role": "group",
                "original_value": "1000.00",
            },
            {
                "asset_name": "Machine A",
                "account_code": "215400",
                "parent_group_key": "account:215400",
                "row_level": 1,
                "acquisition_date": "2025-02-14",
                "original_value": "1000.00",
                "depreciation_amount": "100.00",
                "imported_period_net_value": "900.00",
                "state": "open",
            },
            {
                "label": "218300 — Matériel de bureau et informatique",
                "account_code": "218300",
                "is_group": True,
                "group_key": "account:218300",
                "row_level": 0,
                "presentation_role": "group",
                "original_value": "500.00",
            },
            {
                "asset_name": "Poste de travail",
                "account_code": "218300",
                "parent_group_key": "account:218300",
                "row_level": 1,
                "acquisition_date": "2025-06-19",
                "original_value": "500.00",
                "depreciation_amount": "50.00",
                "imported_period_net_value": "450.00",
                "state": "open",
            },
            {
                "label": "Total des immobilisations",
                "presentation_role": "total",
                "original_value": "1500.00",
            },
        ]
        renderer = self.allowed_env["usl.document.renderer"]
        with (
            patch.object(
                type(self.first_company),
                "_usl_document_renderer_company_payload",
                return_value=(COMPANY_PAYLOAD, []),
            ),
            patch.object(type(renderer), "render", return_value=RENDER_RESULT) as render,
        ):
            wizard._pdf_payload(rows, return_result=True)

        payload = render.call_args.args[2]
        column_labels = [column["label"] for column in payload["columns"]]
        for expected_label in (
            "Compte",
            "Date d’acquisition",
            "Valeur d’origine (€)",
            "Amortissements / provisions (€)",
            "Valeur nette comptable (€)",
            "Statut",
        ):
            self.assertIn(expected_label, column_labels)
        self.assertFalse(
            {"Acquisition Date", "Original Value (€)", "State"}
            & set(column_labels),
        )
        self.assertEqual(
            [section["title"] for section in payload["sections"]],
            [
                "215400 — Matériel industriel",
                "218300 — Matériel de bureau et informatique",
            ],
        )
        self.assertEqual(
            [row["values"]["label"] for row in payload["sections"][-1]["rows"]][-2:],
            [
                "Total — 218300 — Matériel de bureau et informatique",
                "Total des immobilisations",
            ],
        )

    def test_detailed_balance_sheet_adds_an_exact_equality_control(self):
        wizard = self._wizard(report_type="french_balance_sheet_2024")
        rows = [
            {
                "statement_key": "bilan_actif",
                "line_code": "ACTIF_TOTAL",
                "net_amount": "151119.74",
                "presentation_role": "total",
            },
            {
                "statement_key": "bilan_passif",
                "line_code": "PASSIF_TOTAL",
                "net_amount": "151119.74",
                "presentation_role": "total",
            },
        ]

        controlled = wizard._append_shared_control_rows(rows)

        self.assertEqual(controlled[-1]["label"], "Écart actif − passif")
        self.assertEqual(controlled[-1]["net_amount"], "0.00")
        self.assertEqual(controlled[-1]["control_status"], "success")

    def test_professional_packages_receive_governed_front_matter(self):
        wizard = self._wizard(report_type="french_annual", export_format="pdf")
        renderer = self.allowed_env["usl.document.renderer"]
        with (
            patch.object(
                type(self.first_company),
                "_usl_document_renderer_company_payload",
                return_value=(COMPANY_PAYLOAD, []),
            ),
            patch.object(type(renderer), "render", return_value=RENDER_RESULT) as render,
        ):
            wizard._pdf_payload([], return_result=True)

        front = render.call_args.args[2]["front_matter"]
        self.assertEqual(front["status"], "Document préparatoire — non attesté")
        self.assertIn("Bilan — Actif", front["contents"])
        self.assertIn("Bilan — Passif", front["contents"])

    def test_shared_legacy_document_color_uses_governed_accent(self):
        wizard = self._wizard()
        definition = self.env[
            "rebuild.account.report.definition"
        ].with_context(active_test=False).search([
            ("code", "=", "trial_balance"),
            ("company_id", "=", False),
        ], limit=1)
        self.assertTrue(definition)
        self.assertEqual(definition.document_primary_color, "#111111")
        wizard.report_definition_id = definition

        self.assertEqual(wizard._document_theme()["primary_color"], "#714B67")

    def test_combined_report_rejects_different_company_currencies(self):
        usd = self.env.ref("base.USD")
        self.second_company.currency_id = usd
        wizard = self._wizard()

        with self.assertRaisesRegex(
            UserError,
            "Combined reports require companies with the same company currency",
        ):
            wizard._validate_filter_scope()

    def test_interactive_client_preserves_selected_company_scope(self):
        payload = self.allowed_env[
            "rebuild.account.report.export.wizard"
        ].report_client_load(
            "trial_balance",
            {
                "company_id": self.first_company.id,
                "company_ids": [
                    self.first_company.id,
                    self.second_company.id,
                ],
                "date_from": "2026-01-01",
                "date_to": "2026-12-31",
            },
        )

        self.assertTrue(payload["multi_company"])
        self.assertEqual(payload["aggregation_mode"], "aggregate")
        self.assertEqual(
            payload["filters"]["company_ids"],
            [self.first_company.id, self.second_company.id],
        )

    def test_new_report_uses_the_global_selected_company_scope(self):
        payload = self.allowed_env[
            "rebuild.account.report.export.wizard"
        ].report_client_load(
            "trial_balance",
            {
                "date_from": "2026-01-01",
                "date_to": "2026-12-31",
            },
        )

        self.assertTrue(payload["multi_company"])
        self.assertEqual(
            set(payload["filters"]["company_ids"]),
            {self.first_company.id, self.second_company.id},
        )

    def test_interactive_client_moves_primary_company_with_single_scope(self):
        payload = self.allowed_env[
            "rebuild.account.report.export.wizard"
        ].report_client_load(
            "trial_balance",
            {
                "company_id": self.first_company.id,
                "company_ids": [self.second_company.id],
                "date_from": "2026-01-01",
                "date_to": "2026-12-31",
            },
        )

        self.assertFalse(payload["multi_company"])
        self.assertEqual(payload["company_id"], self.second_company.id)
        self.assertEqual(
            payload["filters"]["company_ids"],
            [self.second_company.id],
        )

    def test_interactive_client_rejects_unselected_company_access(self):
        restricted = self.env(context={
            **self.env.context,
            "allowed_company_ids": [self.first_company.id],
        })

        with self.assertRaises(AccessError):
            restricted[
                "rebuild.account.report.export.wizard"
            ].report_client_load(
                "trial_balance",
                {
                    "company_id": self.first_company.id,
                    "company_ids": [
                        self.first_company.id,
                        self.second_company.id,
                    ],
                },
            )

        with self.assertRaises(AccessError):
            restricted[
                "rebuild.account.report.export.wizard"
            ].report_client_load(
                "trial_balance",
                {"company_id": self.second_company.id},
            )
