import base64
from datetime import date

from odoo import Command
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

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
            "pay_month": month,
            "pay_year": year,
            "tese_reference": reference or f"TESE-{year}-{month:02d}",
        })

    def _attach_pdf(self, payslip):
        attachment = self.env["ir.attachment"].sudo().create({
            "name": f"{payslip.tese_reference}.pdf",
            "type": "binary",
            "mimetype": "application/pdf",
            "datas": base64.b64encode(b"%PDF-1.4 TESE payroll test"),
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

    def _posted_payslip(self):
        payslip = self._new_payslip()
        payslip.action_prepare()
        payslip.action_create_draft_entry()
        self._attach_pdf(payslip)
        payslip.action_post()
        return payslip

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

        payslip = self._new_payslip()
        self.profile.sudo().component_line_ids.filtered(
            lambda line: line.code == "421000",
        ).amount = 2299.0
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

    def test_duplicate_exact_and_rounding_difference_use_bank_matching(self):
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
        self.assertIn("exact candidates", payslip.salary_payment_match_message)
        with self.assertRaisesRegex(UserError, "unique exact safe"):
            payslip.action_reconcile_salary()

        payslip.with_context(
            _tese_internal_write=True,
        ).sudo().tese_bank_difference = 0.01
        tese_candidate = {
            "line": self._bank_line(
                1550.0,
                payslip.tese_payment_date,
                self.collector,
                "PRELEVEMENT URSSAF TESE",
            ),
            "amount": 1550.0,
        }
        with self.assertRaisesRegex(UserError, "difference"):
            payslip._create_settlement_bridge(
                "tese",
                tese_candidate,
                payslip._debt_lines("tese"),
            )

    def test_security_requires_combined_roles(self):
        Payslip = self.env["usl.tese.payslip"]
        self.assertFalse(Payslip.with_user(self.hr_only_user).search([]))
        self.assertFalse(Payslip.with_user(self.accounting_only_user).search([]))
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
