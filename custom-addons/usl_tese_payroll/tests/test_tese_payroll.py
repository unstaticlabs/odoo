from datetime import date

from dateutil.relativedelta import relativedelta

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import Form, tagged
from odoo.tools.safe_eval import safe_eval

from ..models.constants import TESE_COMPONENTS
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.addons.mail.tests.common import mail_new_test_user


@tagged("post_install", "-at_install", "usl_tese_payroll")
class TestTesePayroll(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.collector = cls.env["res.partner"].create({
            "name": "URSSAF TESE Test",
            "company_id": cls.company.id,
        })
        cls.payroll_journal = cls.env["account.journal"].create({
            "name": "TESE Payroll Test",
            "code": "TSTP",
            "type": "general",
            "company_id": cls.company.id,
        })
        cls.company.sudo().write({
            "tese_payroll_journal_id": cls.payroll_journal.id,
            "tese_collector_partner_id": cls.collector.id,
        })
        cls.accounts_by_code = {}
        amounts = {
            "641100": 3000.0,
            "645100": 600.0,
            "645200": 50.0,
            "645300": 150.0,
            "633300": 30.0,
            "633500": 20.0,
            "421000": 2300.0,
            "431000": 1100.0,
            "437020": 100.0,
            "437030": 250.0,
            "442100": 100.0,
        }
        cls.amounts = amounts
        for component in TESE_COMPONENTS:
            liability = component["side"] == "credit"
            account = cls.env["account.account"].create({
                "name": f"TESE Test {component['name']}",
                "code": component["code"],
                "account_type": (
                    "liability_current" if liability else "expense"
                ),
                "reconcile": liability,
                "company_ids": [Command.set(cls.company.ids)],
            })
            cls.accounts_by_code[component["code"]] = account

        # Structural HR fixtures need the HR administrator because creating an
        # employee also creates its underlying resource.resource record.
        cls.employee = cls.env["hr.employee"].sudo().create({
            "name": "Alice Payroll",
            "company_id": cls.company.id,
            "work_email": "alice.payroll@example.test",
        })
        cls.employee.version_id.write({
            "date_version": date(2026, 1, 1),
            "contract_date_start": date(2026, 1, 1),
            "wage": 3000.0,
            "hours_per_week": 35.0,
        })
        cls.profile = cls.env["usl.tese.profile"].sudo().create({
            "name": "Alice TESE 2026",
            "company_id": cls.company.id,
            "employee_id": cls.employee.id,
            "hr_version_id": cls.employee.version_id.id,
            "collector_partner_id": cls.collector.id,
            "valid_from": date(2026, 1, 1),
            "default_hours": 151.67,
            "gross_salary": 3000.0,
            "employee_contribution_total": 600.0,
            "employer_contribution_total": 850.0,
            "net_social": 2400.0,
            "net_before_tax": 2400.0,
            "income_tax_base": 2400.0,
            "income_tax_rate": 4.1667,
            "income_tax_amount": 100.0,
            "net_paid": 2300.0,
            "component_line_ids": [
                Command.create({
                    **component,
                    "account_id": cls.accounts_by_code[component["code"]].id,
                    "amount": amounts[component["code"]],
                })
                for component in TESE_COMPONENTS
            ],
        })

        company_commands = [Command.set(cls.company.ids)]
        cls.hr_only_user = mail_new_test_user(
            cls.env,
            name="TESE HR only",
            login="tese_hr_only",
            groups="base.group_user,hr.group_hr_manager",
            company_ids=company_commands,
        )
        cls.accounting_only_user = mail_new_test_user(
            cls.env,
            name="TESE accounting only",
            login="tese_accounting_only",
            groups="base.group_user,account.group_account_readonly",
            company_ids=company_commands,
        )
        cls.readonly_user = mail_new_test_user(
            cls.env,
            name="TESE combined readonly",
            login="tese_combined_readonly",
            groups=(
                "base.group_user,hr.group_hr_manager,"
                "account.group_account_readonly"
            ),
            company_ids=company_commands,
        )
        cls.workflow_user = mail_new_test_user(
            cls.env,
            name="TESE accountant",
            login="tese_accountant",
            groups=(
                "base.group_user,hr.group_hr_manager,"
                "account.group_account_user"
            ),
            company_ids=company_commands,
        )
        cls.config_user = mail_new_test_user(
            cls.env,
            name="TESE configuration manager",
            login="tese_configuration_manager",
            groups=(
                "base.group_user,hr.group_hr_manager,"
                "account.group_account_manager"
            ),
            company_ids=company_commands,
        )
        cls.bank_journal = cls.company_data["default_journal_bank"]
        cls.bank_journal.suspense_account_id.reconcile = True

    def _new_payslip(
        self,
        *,
        month=7,
        year=2026,
        reference=None,
        user=None,
    ):
        user = user or self.workflow_user
        return self.env["usl.tese.payslip"].with_user(user).create({
            "company_id": self.company.id,
            "employee_id": self.employee.id,
            "profile_id": self.profile.id,
            "pay_period": date(year, month, 1),
            "tese_reference": reference or f"TESE-{year}-{month:02d}",
        })

    def _attach_pdf(self, payslip):
        attachment = self.env["ir.attachment"].sudo().create({
            "name": f"{payslip.tese_reference}.pdf",
            "type": "binary",
            "mimetype": "application/pdf",
            "raw": b"%PDF-1.4 TESE payroll test",
            "res_model": payslip._name,
            "res_id": payslip.id,
        })
        payslip.with_user(self.workflow_user).attachment_id = attachment
        return attachment

    def _bank_line(self, amount, payment_date, partner, label):
        statement = self.env["account.bank.statement"].create({
            "journal_id": self.bank_journal.id,
            "date": payment_date,
            "name": f"Statement {label}",
        })
        statement_line = self.env["account.bank.statement.line"].create({
            "name": label,
            "payment_ref": label,
            "journal_id": self.bank_journal.id,
            "statement_id": statement.id,
            "amount": -amount,
            "date": payment_date,
            "partner_id": partner.id,
        })
        self.assertEqual(statement_line.move_id.state, "posted")
        candidate = statement_line.move_id.line_ids.filtered(
            lambda line: (
                line.account_id == self.bank_journal.suspense_account_id
                and line.balance > 0
            ),
        )
        self.assertEqual(len(candidate), 1)
        return candidate

    def test_pdf_picker_is_limited_to_the_payslip_company(self):
        payslip = self._new_payslip()
        own_pdf = self.env["ir.attachment"].sudo().create({
            "name": "same-company-payroll.pdf",
            "type": "binary",
            "mimetype": "application/pdf",
            "raw": b"%PDF-1.4 same company",
            "res_model": payslip._name,
            "res_id": payslip.id,
            "company_id": self.company.id,
        })
        other_company = self.env.ref("base.main_company")
        self.assertNotEqual(other_company, self.company)
        other_employee = self.env["hr.employee"].sudo().create({
            "name": "Other company employee",
            "company_id": other_company.id,
        })
        foreign_pdf = self.env["ir.attachment"].sudo().create({
            "name": "other-company-payroll.pdf",
            "type": "binary",
            "mimetype": "application/pdf",
            "raw": b"%PDF-1.4 other company",
            "res_model": other_employee._name,
            "res_id": other_employee.id,
            "company_id": other_company.id,
        })

        field = self.env["usl.tese.payslip"]._fields["attachment_id"]
        domain = safe_eval(field.domain, {"company_id": self.company.id})
        results = (
            self.env["ir.attachment"]
            .with_user(self.workflow_user)
            .with_context(allowed_company_ids=self.company.ids)
            .web_name_search(
                "",
                {
                    "display_name": {},
                    "name": {},
                    "res_name": {},
                    "res_model": {},
                    "res_id": {},
                    "mimetype": {},
                    "company_id": {},
                },
                domain=domain,
                limit=100,
            )
        )

        self.assertIn(own_pdf.id, {result["id"] for result in results})
        self.assertNotIn(foreign_pdf.id, {result["id"] for result in results})
        with self.assertRaises(UserError), self.cr.savepoint():
            payslip.sudo().attachment_id = foreign_pdf

    def _posted_payslip(self):
        payslip = self._new_payslip()
        payslip.action_prepare()
        payslip.action_create_draft_entry()
        self._attach_pdf(payslip)
        payslip.action_post()
        return payslip

    def test_payroll_evidence_uses_hr_context_and_links_accounting_entry(self):
        payslip = self._posted_payslip()
        context = payslip._document_archive_context(payslip.attachment_id)

        self.assertEqual(context["document_type"], "Payroll record")
        self.assertTrue(context["replace_document_type"])
        self.assertEqual(context["tags"], ["HR", "Payroll"])
        self.assertEqual(context["confidentiality"], "hr")
        self.assertTrue(context["accounting_evidence"])
        self.assertEqual(context["archive_mode"], "mandatory")
        self.assertEqual(context["document_role"], "evidence")
        self.assertEqual(context["policy_reason"], "tese_payroll_evidence")
        self.assertIn(
            {
                "model": "hr.employee",
                "id": payslip.employee_id.id,
                "document_role": "library",
            },
            payslip._document_related_records(payslip.attachment_id),
        )
        self.assertIn(
            {"model": "account.move", "id": payslip.move_id.id},
            payslip._document_related_records(payslip.attachment_id),
        )

    def test_document_reconciliation_cron_is_a_twice_daily_safety_net(self):
        cron = self.env.ref(
            "usl_tese_payroll.ir_cron_tese_reconcile_documents",
        )

        self.assertEqual(cron.interval_number, 12)
        self.assertEqual(cron.interval_type, "hours")

    def test_prepare_snapshots_profile_and_handles_leap_year(self):
        payslip = self._new_payslip(
            month=2,
            year=2028,
            reference="TESE-2028-02",
        )
        payslip.action_prepare()

        self.assertEqual(payslip.state, "prepared")
        self.assertEqual(payslip.period_start, date(2028, 2, 1))
        self.assertEqual(payslip.period_end, date(2028, 2, 29))
        self.assertEqual(payslip.payment_date, date(2028, 3, 1))
        self.assertEqual(payslip.tese_payment_date, date(2028, 4, 15))
        self.assertEqual(payslip.hr_version_id, self.employee.version_id)
        self.assertEqual(len(payslip.component_line_ids), 11)
        self.assertEqual(payslip.total_debit, 3850.0)
        self.assertEqual(payslip.total_credit, 3850.0)
        self.assertEqual(payslip.tese_detailed_total, 1550.0)
        self.assertEqual(payslip.profile_snapshot_label, self.profile.display_name)
        self.assertIn("421000", payslip.profile_snapshot_text)

    def test_profile_overlap_and_formula_errors_block(self):
        with self.assertRaisesRegex(ValidationError, "overlapping"), self.cr.savepoint():
            self.env["usl.tese.profile"].with_user(self.config_user).create({
                "name": "Overlapping profile",
                "company_id": self.company.id,
                "employee_id": self.employee.id,
                "valid_from": date(2026, 6, 1),
            })

        self.profile.sudo().component_line_ids.filtered(
            lambda line: line.code == "421000",
        ).amount = 2299.0
        payslip = self._new_payslip()
        with self.assertRaisesRegex(
            ValidationError,
            "not balanced",
        ), self.cr.savepoint():
            payslip.action_prepare()

    def test_pdf_is_required_and_posted_history_is_immutable(self):
        payslip = self._new_payslip()
        payslip.action_prepare()
        payslip.action_create_draft_entry()
        self.assertEqual(payslip.state, "to_post")
        with self.assertRaisesRegex(UserError, "PDF"):
            payslip.action_post()

        attachment = self._attach_pdf(payslip)
        payslip.action_post()
        self.assertEqual(payslip.state, "to_reconcile")
        self.assertEqual(payslip.move_id.state, "posted")
        self.assertEqual(payslip.move_id.tese_payslip_id, payslip)
        self.assertEqual(payslip.move_id.tese_attachment_id, attachment)
        salary_line = payslip.move_id.line_ids.filtered(
            lambda line: line.account_id.code == "421000",
        )
        cost_lines = payslip.move_id.line_ids.filtered(
            lambda line: line.account_id.code.startswith(("6",)),
        )
        self.assertEqual(salary_line.partner_id, self.employee.work_contact_id)
        self.assertTrue(all(
            line.partner_id == self.employee.work_contact_id
            for line in cost_lines
        ))

        with self.assertRaisesRegex(UserError, "cannot be changed"):
            payslip.with_context(_tese_internal_write=True).gross_salary = 1.0
        with self.assertRaisesRegex(UserError, "immutable"):
            payslip.move_id.button_draft()
        with self.assertRaisesRegex(UserError, "cannot be deleted"):
            payslip.move_id.unlink()

    def test_unique_exact_salary_and_tese_matches_create_safe_bridges(self):
        payslip = self._posted_payslip()
        salary_candidate = self._bank_line(
            2300.0,
            payslip.payment_date,
            self.employee.work_contact_id,
            "VIR ALICE PAYROLL",
        )
        tese_candidate = self._bank_line(
            1550.0,
            payslip.tese_payment_date,
            self.collector,
            "PRELEVEMENT URSSAF TESE",
        )

        payslip.action_refresh_candidates()
        self.assertEqual(payslip.salary_payment_best_line_id, salary_candidate)
        self.assertEqual(payslip.tese_payment_best_line_id, tese_candidate)
        self.assertIn("unique exact safe", payslip.salary_payment_match_message)

        payslip.action_reconcile_salary()
        self.assertTrue(payslip.salary_payment_reconciled)
        self.assertTrue(payslip.salary_settlement_move_id)
        self.assertEqual(
            payslip.salary_settlement_move_id.tese_move_role,
            "salary_settlement",
        )
        self.assertEqual(payslip.state, "to_reconcile")

        payslip.action_reconcile_tese()
        self.assertTrue(payslip.tese_payment_reconciled)
        self.assertTrue(payslip.tese_settlement_move_id)
        self.assertEqual(
            payslip.tese_settlement_move_id.tese_move_role,
            "tese_settlement",
        )
        self.assertEqual(payslip.state, "paid")
        self.assertTrue(payslip.payment_check_ok)
        self.assertTrue(all(
            line.reconciled
            for line in payslip._debt_lines("salary")
            | payslip._debt_lines("tese")
        ))

    def test_duplicate_exact_requires_bank_matching(self):
        payslip = self._posted_payslip()
        self._bank_line(
            2300.0,
            payslip.payment_date,
            self.employee.work_contact_id,
            "VIR ALICE PAYROLL A",
        )
        self._bank_line(
            2300.0,
            payslip.payment_date,
            self.employee.work_contact_id,
            "VIR ALICE PAYROLL B",
        )
        payslip.action_refresh_candidates()
        self.assertEqual(payslip.salary_payment_candidate_count, 2)
        self.assertIn(
            "plausible candidates",
            payslip.salary_payment_match_message,
        )
        with self.assertRaisesRegex(UserError, "unique safe"):
            payslip.action_reconcile_salary()

    def test_guided_period_uses_oldest_missing_completed_month(self):
        for month in range(1, 6):
            self._new_payslip(
                month=month,
                reference=f"GUIDED-2026-{month:02d}",
            )
        suggested = self.env["usl.tese.payslip"]._suggest_pay_period(
            self.employee,
            self.company,
            today=date(2026, 7, 1),
        )
        self.assertEqual(suggested, date(2026, 6, 1))

        june = self._new_payslip(month=6, reference="GUIDED-2026-06")
        self.assertEqual(june.pay_period, date(2026, 6, 1))
        self.env.flush_all()
        suggested = self.env["usl.tese.payslip"]._suggest_pay_period(
            self.employee,
            self.company,
            today=date(2026, 7, 1),
        )
        self.assertEqual(suggested, date(2026, 7, 1))
        self._new_payslip(month=7, reference="GUIDED-2026-07")
        with self.assertRaisesRegex(UserError, "already exists"):
            self.env["usl.tese.payslip"]._suggest_pay_period(
                self.employee,
                self.company,
                today=date(2026, 7, 1),
            )

    def test_period_is_normalized_and_dates_default_without_overriding_manual(self):
        payslip = self.env["usl.tese.payslip"].with_user(
            self.workflow_user,
        ).create({
            "company_id": self.company.id,
            "employee_id": self.employee.id,
            "profile_id": self.profile.id,
            "pay_period": date(2028, 2, 20),
            "payment_date": date(2028, 3, 3),
            "tese_payment_date": date(2028, 4, 18),
            "tese_reference": "TESE-MANUAL-DATES",
        })
        self.assertEqual(payslip.pay_period, date(2028, 2, 1))
        self.assertEqual(payslip.period_end, date(2028, 2, 29))
        self.assertEqual(payslip.payment_date, date(2028, 3, 3))
        self.assertEqual(payslip.tese_payment_date, date(2028, 4, 18))

    def test_new_draft_prefills_recurring_provider_figures(self):
        defaults = self.env["usl.tese.payslip"].with_user(
            self.workflow_user,
        ).with_context(
            default_employee_id=self.employee.id,
        ).default_get([
            "company_id",
            "employee_id",
            "profile_id",
            "pay_period",
            "gross_salary",
            "net_paid",
            "tese_detailed_total",
            "tese_bank_amount",
            "tese_payment_date",
        ])
        self.assertEqual(defaults["profile_id"], self.profile.id)
        self.assertEqual(defaults["gross_salary"], 3000.0)
        self.assertEqual(defaults["net_paid"], 2300.0)
        self.assertEqual(defaults["tese_detailed_total"], 1550.0)
        self.assertEqual(defaults["tese_bank_amount"], 1550.0)
        self.assertEqual(
            defaults["tese_payment_date"],
            defaults["pay_period"] + relativedelta(months=2, day=15),
        )

    def test_tese_hr_difference_is_visible_while_configuring(self):
        self.profile.with_user(self.config_user).write({
            "default_hours": 160.0,
        })
        self.assertIn(
            "Monthly hours — TESE: 160.00 · HR: 151.67.",
            self.profile.hr_mismatch_warning,
        )
        self.assertIn(
            "Next: align TESE with HR",
            self.profile.hr_mismatch_warning,
        )
        self.assertTrue(self.profile.has_hr_mismatch)
        self.assertEqual(self.profile.display_review_status, "warning")

        payslip = self.env["usl.tese.payslip"].with_user(
            self.workflow_user,
        ).create({
            "company_id": self.company.id,
            "employee_id": self.employee.id,
            "profile_id": self.profile.id,
            "pay_period": date(2026, 6, 1),
            "tese_reference": "TESE-HR-VISIBLE-DIFFERENCE",
        })
        self.assertEqual(
            payslip.profile_mismatch_warning,
            self.profile.hr_mismatch_warning,
        )

        with Form(
            self.env["usl.tese.settings.revision.wizard"].with_user(
                self.config_user,
            ).with_context(default_payslip_id=payslip.id),
        ) as wizard_form:
            wizard = wizard_form.save()
        self.assertIn(
            "Monthly hours — TESE: 160.00 · HR: 151.67.",
            wizard.comparison_warning,
        )
        wizard.default_hours = 35.0 * 52.0 / 12.0
        self.assertFalse(wizard.comparison_warning)

    def test_urssaf_rounding_settles_payroll_and_carries_431_credit(self):
        payslip = self._posted_payslip()
        self._bank_line(
            2300.0,
            payslip.payment_date,
            self.employee.work_contact_id,
            "VIR ALICE PAYROLL",
        )
        self._bank_line(
            1550.55,
            payslip.tese_payment_date,
            self.collector,
            "PRELEVEMENT URSSAF TESE",
        )
        payslip.action_refresh_candidates()
        payslip.action_reconcile_salary()
        payslip.action_reconcile_tese()

        self.assertEqual(payslip.tese_bank_amount, 1550.55)
        self.assertEqual(payslip.tese_bank_difference, 0.55)
        self.assertEqual(payslip.state, "paid")
        self.assertEqual(payslip.payment_status, "paid")
        self.assertTrue(payslip.payment_check_ok)
        self.assertTrue(payslip.tese_payment_reconciled)
        self.assertEqual(payslip.tese_open_amount, 0.0)
        self.assertEqual(payslip.rounding_open_amount, 0.55)
        self.assertIn("credit", payslip.payment_check_message)
        self.assertIn("No payroll action", payslip.payment_check_message)
        self.assertEqual(
            payslip.rounding_carryover_message,
            payslip.payment_check_message,
        )
        rounding_lines = payslip.tese_settlement_move_id.line_ids.filtered(
            lambda line: (
                line.account_id.code == "431000"
                and not payslip.currency_id.is_zero(line.amount_residual)
            ),
        )
        self.assertEqual(len(rounding_lines), 1)
        self.assertEqual(abs(rounding_lines.amount_residual), 0.55)
        issues = self.env["usl.tese.diagnostic.issue"]._collect_company_issues(
            self.company,
        )
        self.assertNotIn(f"payslip:{payslip.id}:residual", issues)

    def test_urssaf_rounding_settles_payroll_and_carries_431_due(self):
        payslip = self._posted_payslip()
        self._bank_line(
            2300.0,
            payslip.payment_date,
            self.employee.work_contact_id,
            "VIR ALICE PAYROLL",
        )
        self._bank_line(
            1549.45,
            payslip.tese_payment_date,
            self.collector,
            "PRELEVEMENT URSSAF TESE",
        )
        payslip.action_refresh_candidates()
        payslip.action_reconcile_salary()
        payslip.action_reconcile_tese()

        self.assertEqual(payslip.tese_bank_difference, -0.55)
        self.assertEqual(payslip.state, "paid")
        self.assertEqual(payslip.payment_status, "paid")
        self.assertTrue(payslip.payment_check_ok)
        self.assertEqual(payslip.tese_open_amount, 0.0)
        self.assertEqual(payslip.rounding_open_amount, 0.55)
        self.assertIn("due", payslip.payment_check_message)
        rounding_lines = payslip._tracked_liability_lines("tese").filtered(
            lambda line: line.account_id.code == "431000",
        )
        self.assertEqual(len(rounding_lines), 1)
        self.assertEqual(abs(rounding_lines.amount_residual), 0.55)

    def test_urssaf_rounding_above_five_euros_is_not_automatic(self):
        payslip = self._posted_payslip()
        self._bank_line(
            1555.01,
            payslip.tese_payment_date,
            self.collector,
            "PRELEVEMENT URSSAF TESE",
        )
        payslip.action_refresh_candidates()
        self.assertFalse(payslip.tese_payment_best_line_id)
        with self.assertRaisesRegex(UserError, "unique safe"):
            payslip.action_reconcile_tese()

    def test_external_reconciliation_updates_payment_badge_from_residuals(self):
        payslip = self._posted_payslip()
        self.assertEqual(payslip.payment_status, "open_both")
        salary_debt = payslip._debt_lines("salary")
        external_move = self.env["account.move"].create({
            "move_type": "entry",
            "company_id": self.company.id,
            "journal_id": self.payroll_journal.id,
            "date": payslip.payment_date,
            "ref": "External Bank Matching salary settlement",
            "line_ids": [
                Command.create({
                    "name": "Salary paid through Bank Matching",
                    "account_id": salary_debt.account_id.id,
                    "partner_id": self.employee.work_contact_id.id,
                    "debit": 2300.0,
                }),
                Command.create({
                    "name": "Bank counterpart",
                    "account_id": self.bank_journal.suspense_account_id.id,
                    "credit": 2300.0,
                }),
            ],
        })
        external_move.action_post()
        external_salary = external_move.line_ids.filtered(
            lambda line: line.account_id == salary_debt.account_id,
        )
        (salary_debt + external_salary).reconcile()

        self.assertEqual(payslip.salary_open_amount, 0.0)
        self.assertEqual(payslip.payment_status, "tese_open")

    def test_combined_revision_creates_contract_and_profile_versions(self):
        payslip = self._new_payslip()
        Wizard = self.env["usl.tese.settings.revision.wizard"].with_user(
            self.config_user,
        ).with_context(
            default_payslip_id=payslip.id,
            default_profile_id=self.profile.id,
            default_employee_id=self.employee.id,
            default_effective_period=payslip.pay_period,
        )
        with Form(Wizard) as wizard_form:
            wizard_form.update_contract = True
            wizard_form.wage = 3100.0
            wizard = wizard_form.save()
        result = wizard.action_apply()

        self.assertEqual(payslip.state, "prepared")
        self.assertNotEqual(payslip.profile_id, self.profile)
        self.assertFalse(self.profile.active)
        self.assertEqual(self.profile.valid_to, date(2026, 6, 30))
        self.assertEqual(payslip.profile_id.valid_from, date(2026, 7, 1))
        self.assertEqual(payslip.hr_version_id.date_version, date(2026, 7, 1))
        self.assertEqual(payslip.hr_version_id.wage, 3100.0)
        self.assertEqual(
            payslip.profile_id.hr_version_id,
            payslip.hr_version_id,
        )
        self.assertEqual(len(payslip.profile_id.component_line_ids), 11)
        self.assertEqual(result["tag"], "display_notification")
        self.assertIn("archived, not deleted", result["params"]["message"])
        self.assertEqual(
            result["params"]["next"]["res_id"],
            payslip.id,
        )

    def test_combined_revision_creates_first_contract_when_missing(self):
        employee = self.env["hr.employee"].sudo().create({
            "name": "Bob First Contract",
            "company_id": self.company.id,
            "work_email": "bob.first.contract@example.test",
        })
        self.assertFalse(employee._is_in_contract(date(2026, 7, 1)))
        profile = self.profile.with_user(self.config_user).copy(default={
            "name": "Bob TESE 2026",
            "employee_id": employee.id,
            "hr_version_id": False,
            "last_used_date": False,
        })
        payslip = self.env["usl.tese.payslip"].with_user(
            self.workflow_user,
        ).create({
            "company_id": self.company.id,
            "employee_id": employee.id,
            "profile_id": profile.id,
            "pay_period": date(2026, 7, 1),
            "tese_reference": "TESE-FIRST-CONTRACT",
        })
        Wizard = self.env["usl.tese.settings.revision.wizard"].with_user(
            self.config_user,
        ).with_context(
            default_payslip_id=payslip.id,
            default_profile_id=profile.id,
            default_employee_id=employee.id,
            default_effective_period=payslip.pay_period,
        )
        with Form(Wizard) as wizard_form:
            wizard_form.update_contract = True
            wizard_form.wage = 2800.0
            wizard = wizard_form.save()
        wizard.action_apply()

        self.assertTrue(employee._is_in_contract(date(2026, 7, 1)))
        self.assertEqual(
            payslip.hr_version_id.contract_date_start,
            date(2026, 7, 1),
        )
        self.assertEqual(payslip.hr_version_id.wage, 2800.0)
        self.assertEqual(payslip.profile_id.hr_version_id, payslip.hr_version_id)

    def test_same_month_profile_revision_closes_archived_profile(self):
        payslip = self._new_payslip(month=1, reference="SAME-MONTH-REVISION")
        wizard = self.env[
            "usl.tese.settings.revision.wizard"
        ].with_user(self.config_user).with_context(
            default_payslip_id=payslip.id,
            default_profile_id=self.profile.id,
            default_employee_id=self.employee.id,
            default_effective_period=payslip.pay_period,
        ).create({})

        wizard.action_apply()

        self.assertFalse(self.profile.active)
        self.assertEqual(self.profile.valid_from, date(2026, 1, 1))
        self.assertEqual(self.profile.valid_to, date(2026, 1, 1))

    def test_bank_matching_opens_without_refreshed_candidates(self):
        payslip = self._posted_payslip()

        action = payslip.action_open_bank_matching()

        self.assertEqual(action["res_model"], "account.bank.statement.line")
        self.assertEqual(
            action["domain"],
            [("company_id", "=", self.company.id)],
        )
        self.assertTrue(action["context"]["search_default_not_reconciled"])

    def test_combined_revision_refreshes_existing_draft_entry(self):
        payslip = self._new_payslip()
        payslip.action_prepare()
        payslip.action_create_draft_entry()
        draft_move = payslip.move_id
        Wizard = self.env["usl.tese.settings.revision.wizard"].with_user(
            self.config_user,
        ).with_context(
            default_payslip_id=payslip.id,
            default_profile_id=self.profile.id,
            default_employee_id=self.employee.id,
            default_effective_period=payslip.pay_period,
        )
        with Form(Wizard) as wizard_form:
            wizard_form.update_contract = True
            wizard_form.wage = 3200.0
            wizard = wizard_form.save()
        wizard.action_apply()

        self.assertEqual(payslip.state, "to_post")
        self.assertEqual(payslip.move_id, draft_move)
        self.assertEqual(draft_move.state, "draft")

    def test_used_profile_requires_a_dated_revision(self):
        self._new_payslip()
        with self.assertRaisesRegex(UserError, "next dated version"):
            self.profile.with_user(self.config_user).gross_salary = 3100.0
        with self.assertRaisesRegex(UserError, "next dated settings version"):
            self.profile.component_line_ids[:1].with_user(
                self.config_user,
            ).amount = 1.0

    def test_security_requires_combined_roles(self):
        Payslip = self.env["usl.tese.payslip"]
        with self.assertRaises(AccessError):
            Payslip.with_user(self.hr_only_user).search([])
        with self.assertRaises(AccessError):
            Payslip.with_user(self.accounting_only_user).search([])
        self.assertEqual(
            Payslip.with_user(self.readonly_user).search([]),
            self.env["usl.tese.payslip"],
        )
        with self.assertRaises(AccessError):
            self._new_payslip(
                reference="TESE-READONLY",
                user=self.readonly_user,
            )
        with self.assertRaisesRegex(AccessError, "Accounting Administrator"):
            self.profile.with_user(self.workflow_user).name = "Forbidden"

        created = self._new_payslip(reference="TESE-COMBINED")
        created.with_user(self.readonly_user).check_access("read")
        with self.assertRaises(AccessError):
            created.with_user(self.hr_only_user).check_access("read")

        other_company = self.env["res.company"].sudo().create({
            "name": "Other TESE company",
            "currency_id": self.company.currency_id.id,
        })
        other_issue = self.env["usl.tese.diagnostic.issue"].sudo().create({
            "name": "Other company issue",
            "stable_key": "test:other-company",
            "severity": "warning",
            "category": "configuration",
            "message": "This issue belongs to another company.",
            "first_seen_at": fields.Datetime.now(),
            "last_seen_at": fields.Datetime.now(),
            "company_id": other_company.id,
        })
        self.assertFalse(
            self.env["usl.tese.diagnostic.issue"].with_user(
                self.config_user,
            ).search([("id", "=", other_issue.id)]),
        )
        with self.assertRaises(ValidationError):
            Payslip.sudo().create({
                "company_id": other_company.id,
                "employee_id": self.employee.id,
                "profile_id": self.profile.id,
                "pay_period": date(2026, 9, 1),
                "tese_reference": "TESE-CROSS-COMPANY",
            })

    def test_navigation_opens_all_payroll_and_dedicated_configuration(self):
        payroll_action = self.env.ref(
            "usl_tese_payroll.action_tese_payslips",
        )
        self.assertNotIn(
            "search_default_needs_attention",
            safe_eval(payroll_action.context or "{}"),
        )
        payroll_form = self.env.ref(
            "usl_tese_payroll.view_tese_payslip_form",
        ).arch_db
        payroll_search = self.env.ref(
            "usl_tese_payroll.view_tese_payslip_search",
        ).arch_db
        self.assertNotIn("action_open_rounding_reconciliation", payroll_form)
        self.assertIn("rounding_carryover_message", payroll_form)
        self.assertIn("text-bg-success\">Matched", payroll_form)
        self.assertIn("URSSAF Carry-over", payroll_search)
        self.assertNotIn("'rounding_open'", payroll_search)
        self.assertIn(
            "usl.tese.payslip",
            self.env["usl.document.link"]._allowed_models(),
        )
        self.assertIn(
            "archived_document_count",
            self.env["usl.tese.payslip"]._fields,
        )
        self.assertEqual(
            payroll_form.count('name="action_open_documents_workspace"'),
            1,
        )

        configuration_menu = self.env.ref(
            "usl_tese_payroll.menu_tese_payroll_configuration",
        )
        self.assertFalse(configuration_menu.action)
        for menu_xmlid in (
            "menu_tese_payroll_settings",
            "menu_tese_payroll_diagnostics",
            "menu_tese_payroll_accounts",
        ):
            self.assertEqual(
                self.env.ref(f"usl_tese_payroll.{menu_xmlid}").parent_id,
                configuration_menu,
            )
        diagnostics_menu = self.env.ref(
            "usl_tese_payroll.menu_tese_payroll_diagnostics",
        )
        self.assertEqual(
            diagnostics_menu.action,
            self.env.ref("usl_tese_payroll.action_run_tese_diagnostics"),
        )
        self.assertFalse(
            self.env.ref(
                "usl_tese_payroll.menu_tese_payroll_run_diagnostics",
            ).active,
        )

        profiles_action = self.env.ref(
            "usl_tese_payroll.action_tese_profiles",
        )
        profiles_context = safe_eval(profiles_action.context or "{}")
        self.assertFalse(profiles_context["active_test"])
        self.assertEqual(profiles_context["search_default_active_profiles"], 1)
        profiles_domain = safe_eval(profiles_action.domain or "[]")
        archived_profile = self.profile.with_user(self.config_user).copy(default={
            "name": "Archived profile visible from history",
            "active": False,
            "review_status": "archived",
        })
        self.assertIn(
            archived_profile,
            self.env["usl.tese.profile"].with_user(
                self.config_user,
            ).search(profiles_domain),
        )

        accounts_menu = self.env.ref(
            "usl_tese_payroll.menu_tese_payroll_accounts",
        )
        self.assertIn(
            self.env.ref("account.group_account_manager"),
            accounts_menu.group_ids,
        )
        accounts_action = self.env.ref(
            "usl_tese_payroll.action_tese_payroll_accounts",
        )
        self.assertEqual(accounts_action.res_model, "account.account")
        self.assertEqual(accounts_action.view_mode, "list,form")
        self.assertEqual(
            accounts_action.view_id,
            self.env.ref("account.view_account_list"),
        )
        self.assertEqual(
            accounts_action.search_view_id,
            self.env.ref("account.view_account_search"),
        )
        payroll_accounts = self.env["account.account"].with_user(
            self.config_user,
        ).search(safe_eval(accounts_action.domain))
        self.assertEqual(
            set(payroll_accounts.mapped("code")),
            {component["code"] for component in TESE_COMPONENTS},
        )
        self.assertEqual(
            set(payroll_accounts.ids),
            {account.id for account in self.accounts_by_code.values()},
        )

        settings_action = self.env.ref(
            "usl_tese_payroll.action_open_tese_settings",
        ).with_user(self.config_user)
        result = settings_action.run()
        self.assertEqual(result["res_model"], "res.company")
        self.assertEqual(result["res_id"], self.company.id)
        self.assertEqual(
            result["views"][0][0],
            self.env.ref(
                "usl_tese_payroll.view_company_form_tese_configuration",
            ).id,
        )
        self.assertGreater(
            self.env.ref(
                "usl_tese_payroll.view_company_form_tese_configuration",
            ).priority,
            self.env.ref("base.view_company_form").priority,
            "The dedicated TESE form must not replace native Company settings",
        )
        with self.assertRaisesRegex(AccessError, "Accounting Administrator"):
            self.company.with_user(
                self.workflow_user,
            ).action_open_tese_configuration()

    def test_diagnostics_retain_resolved_history(self):
        payslip = self._new_payslip()
        payslip.action_prepare()
        payslip.action_create_draft_entry()
        Diagnostic = self.env["usl.tese.diagnostic.issue"].with_user(
            self.workflow_user,
        )
        action = self.env.ref(
            "usl_tese_payroll.action_run_tese_diagnostics",
        ).with_user(self.workflow_user)
        result = action.run()
        self.assertEqual(result["res_model"], "usl.tese.diagnostic.issue")
        self.assertFalse(result["context"]["active_test"])
        self.assertIn(("active", "=", False), result["domain"])
        issue = self.env["usl.tese.diagnostic.issue"].sudo().search([
            ("stable_key", "=", f"payslip:{payslip.id}:pdf"),
        ])
        self.assertTrue(issue.active)
        self.assertEqual(issue.severity, "blocking")

        self._attach_pdf(payslip)
        Diagnostic.action_run_diagnostics()
        self.assertFalse(issue.active)
        self.assertTrue(issue.resolved)
        self.assertTrue(issue.resolved_at)
