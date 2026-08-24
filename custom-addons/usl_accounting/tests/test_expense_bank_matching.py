from datetime import date, timedelta

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from odoo.addons.hr_expense.tests.common import TestExpenseCommon
from odoo.addons.mail.tests.common import mail_new_test_user


@tagged(
    "post_install",
    "-at_install",
    "usl_accounting_unit",
    "usl_expense_bank_matching",
)
class TestExpenseBankMatching(TestExpenseCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.match_date = date(2026, 7, 10)
        cls.bank_journal = cls.company_data["default_journal_bank"]
        cls.outbound_method = (
            cls.bank_journal.outbound_payment_method_line_ids.filtered(
                lambda method: method.code == "manual",
            )[:1]
            or cls.bank_journal.outbound_payment_method_line_ids[:1]
        )
        if not cls.outbound_method:
            message = "The test bank journal needs an outbound method."
            raise AssertionError(message)
        if not cls.outbound_method.payment_account_id:
            cls.outbound_method.payment_account_id = cls.env[
                "account.chart.template"
            ].ref("account_journal_payment_credit_account_id")
        cls.env.company.company_expense_allowed_payment_method_line_ids = [
            Command.set(cls.outbound_method.ids),
        ]
        cls.vendor = cls.env["res.partner"].create({
            "name": "Toronto Hotel",
        })
        cls.readonly_user = mail_new_test_user(
            cls.env,
            name="Expense evidence reviewer",
            login="expense_bank_match_readonly",
            groups="base.group_user,account.group_account_readonly",
            company_ids=[Command.set(cls.env.company.ids)],
        )

    def _expense(
        self,
        name="Toronto Hotel",
        *,
        amount=100.0,
        expense_date=None,
        vendor=None,
        currency=None,
        with_receipt=True,
    ):
        currency = currency or self.env.company.currency_id
        expense = self.env["hr.expense"].create({
            "name": name,
            "date": expense_date or self.match_date,
            "employee_id": self.expense_employee.id,
            "company_id": self.env.company.id,
            "currency_id": currency.id,
            "product_id": self.product_c.id,
            "total_amount_currency": amount,
            "payment_mode": "own_account",
            "vendor_id": (vendor or self.vendor).id,
            "analytic_distribution": {
                str(self.analytic_account_1.id): 100,
            },
        })
        if with_receipt:
            attachment = self.env["ir.attachment"].sudo().create({
                "name": f"{name}.pdf",
                "type": "binary",
                "raw": b"expense matching test receipt",
                "res_model": "hr.expense",
                "res_id": expense.id,
            })
            expense.sudo().message_main_attachment_id = attachment
            expense.invalidate_recordset(["message_main_attachment_id"])
        return expense

    def _bank_line(
        self,
        *,
        amount=-100.0,
        bank_date=None,
        label="CB TORONTO HOTEL",
        partner=None,
        journal=None,
        foreign_currency=None,
        amount_currency=0.0,
    ):
        journal = journal or self.bank_journal
        statement = self.env["account.bank.statement"].create({
            "journal_id": journal.id,
            "date": bank_date or self.match_date,
            "name": f"Statement {label}",
        })
        return self.env["account.bank.statement.line"].create({
            "name": label,
            "payment_ref": label,
            "journal_id": journal.id,
            "statement_id": statement.id,
            "amount": amount,
            "foreign_currency_id": (
                foreign_currency.id if foreign_currency else False
            ),
            "amount_currency": amount_currency,
            "date": bank_date or self.match_date,
            "partner_id": partner.id if partner else False,
        })

    def test_refresh_ranks_five_candidates_and_is_idempotent(self):
        expense = self._expense()
        exact = self._bank_line(partner=self.vendor)
        for offset in range(1, 7):
            self._bank_line(
                amount=-(100 + offset / 10),
                bank_date=self.match_date + timedelta(days=offset),
                label=f"Alternative {offset}",
            )

        expense._usl_refresh_bank_match_candidates()
        available = expense.usl_bank_match_candidate_ids.filtered(
            lambda candidate: candidate.state == "available",
        )
        self.assertEqual(len(available), 5)
        self.assertEqual(available.sorted("rank")[0].bank_statement_line_id, exact)
        self.assertEqual(available.sorted("rank")[0].match_label, "best")
        self.assertIn(
            "Exact amount",
            available.sorted("rank")[0].evidence_summary,
        )
        candidate_ids = available.ids

        expense._usl_refresh_bank_match_candidates()
        refreshed = expense.usl_bank_match_candidate_ids.filtered(
            lambda candidate: candidate.state == "available",
        )
        self.assertEqual(refreshed.ids, candidate_ids)
        self.assertEqual(
            len(expense.usl_bank_match_candidate_ids),
            len(set(expense.usl_bank_match_candidate_ids.ids)),
        )

    def test_near_amount_is_visible_but_not_actionable(self):
        expense = self._expense(amount=100)
        self._bank_line(amount=-101)
        expense._usl_refresh_bank_match_candidates()
        candidate = expense.usl_bank_match_candidate_ids.filtered(
            lambda item: item.state == "available",
        )
        self.assertEqual(len(candidate), 1)
        self.assertFalse(candidate.amount_is_exact)
        with self.assertRaisesRegex(UserError, "close but not equal"):
            candidate.action_open_confirmation()

    def test_explicit_bank_foreign_currency_is_matched_without_guessing(self):
        expense = self._expense(
            name="Foreign currency expense",
            amount=1_000,
            currency=self.other_currency,
        )
        bank_line = self._bank_line(
            amount=-40,
            foreign_currency=self.other_currency,
            amount_currency=-1_000,
            label="Foreign currency expense",
        )

        expense._usl_refresh_bank_match_candidates()
        candidate = expense.usl_bank_match_candidate_ids

        self.assertEqual(candidate.bank_statement_line_id, bank_line)
        self.assertTrue(candidate.amount_is_exact)
        self.assertEqual(candidate.bank_amount, 1_000)

    def test_changed_source_facts_make_candidate_stale(self):
        expense = self._expense()
        self._bank_line()
        expense._usl_refresh_bank_match_candidates()
        candidate = expense.usl_bank_match_candidate_ids
        expense.total_amount_currency = 101
        with self.assertRaisesRegex(UserError, "changed after"):
            candidate.action_open_confirmation()

    def test_reconciled_positive_and_out_of_window_lines_are_excluded(self):
        expense = self._expense()
        self._bank_line(amount=100)
        self._bank_line(
            bank_date=self.match_date + timedelta(days=11),
        )
        expense._usl_refresh_bank_match_candidates()
        self.assertFalse(expense.usl_bank_match_candidate_ids)

    def test_readonly_accountant_can_inspect_but_not_refresh_or_use(self):
        expense = self._expense()
        self._bank_line()
        expense._usl_refresh_bank_match_candidates()
        candidate = expense.usl_bank_match_candidate_ids

        readonly_candidate = candidate.with_user(self.readonly_user)
        self.assertEqual(readonly_candidate.evidence_summary, candidate.evidence_summary)
        with self.assertRaises(AccessError):
            expense.with_user(
                self.readonly_user,
            ).action_refresh_bank_match_candidates()
        with self.assertRaises(AccessError):
            readonly_candidate.action_open_confirmation()

    def test_candidate_evidence_is_company_scoped(self):
        other_company = self.company_data_2["company"]
        other_journal = self.company_data_2["default_journal_bank"]
        allowed_company_ids = (self.env.company | other_company).ids
        other_employee = self.env["hr.employee"].sudo().create({
            "name": "Other company expense employee",
            "company_id": other_company.id,
        })
        expense = self.env["hr.expense"].with_context(
            allowed_company_ids=allowed_company_ids,
        ).with_company(other_company).create({
            "name": "Other company hotel",
            "date": self.match_date,
            "employee_id": other_employee.id,
            "company_id": other_company.id,
            "currency_id": other_company.currency_id.id,
            "account_id": self.company_data_2[
                "default_account_expense"
            ].id,
            "total_amount_currency": 100,
            "payment_mode": "own_account",
        })
        statement = self.env["account.bank.statement"].with_context(
            allowed_company_ids=allowed_company_ids,
        ).with_company(other_company).create({
            "journal_id": other_journal.id,
            "date": self.match_date,
            "name": "Other company statement",
        })
        self.env["account.bank.statement.line"].with_context(
            allowed_company_ids=allowed_company_ids,
        ).with_company(other_company).create({
            "name": "Other company hotel",
            "payment_ref": "Other company hotel",
            "journal_id": other_journal.id,
            "statement_id": statement.id,
            "amount": -100,
            "date": self.match_date,
        })

        expense.with_context(
            allowed_company_ids=allowed_company_ids,
        )._usl_refresh_bank_match_candidates()
        candidate = expense.usl_bank_match_candidate_ids

        readonly_candidate = candidate.with_user(
            self.readonly_user,
        ).with_context(allowed_company_ids=self.env.company.ids)
        with self.assertRaises(AccessError):
            readonly_candidate.read(["evidence_summary"])

    def test_competing_expenses_are_disclosed(self):
        first = self._expense(name="Toronto Hotel first")
        second = self._expense(name="Toronto Hotel second")
        line = self._bank_line()

        first._usl_refresh_bank_match_candidates()
        second._usl_refresh_bank_match_candidates()
        first_candidate = first.usl_bank_match_candidate_ids.filtered(
            lambda candidate: candidate.bank_statement_line_id == line,
        )
        second_candidate = second.usl_bank_match_candidate_ids.filtered(
            lambda candidate: candidate.bank_statement_line_id == line,
        )
        self.assertEqual(first_candidate.competing_expense_count, 1)
        self.assertEqual(second_candidate.competing_expense_count, 1)
        self.assertIn("other expense", first_candidate.evidence_summary)

    def test_one_click_uses_native_payment_and_oca_reconciliation(self):
        expense = self._expense()
        bank_line = self._bank_line(partner=self.vendor)
        expense._usl_refresh_bank_match_candidates()
        candidate = expense.usl_bank_match_candidate_ids.filtered(
            lambda item: item.bank_statement_line_id == bank_line,
        )

        action = candidate._apply_match()

        expense.invalidate_recordset([
            "state",
            "payment_mode",
            "payment_method_line_id",
            "account_move_id",
        ])
        bank_line.invalidate_recordset(["is_reconciled"])
        self.assertEqual(action["res_model"], "hr.expense")
        self.assertEqual(expense.payment_mode, "company_account")
        self.assertEqual(expense.payment_method_line_id, self.outbound_method)
        self.assertTrue(expense.account_move_id.origin_payment_id)
        self.assertEqual(expense.account_move_id.state, "posted")
        self.assertTrue(bank_line.is_reconciled)
        self.assertEqual(candidate.state, "accepted")
        self.assertEqual(candidate.accepted_by_id, self.env.user)
        self.assertTrue(
            expense.message_ids.filtered(
                lambda message: "Matched company-paid expense"
                in (message.body or ""),
            ),
        )

    def test_submitted_and_approved_expenses_use_native_lifecycle(self):
        submitted = self._expense(
            name="Submitted company expense",
            amount=113,
            expense_date=self.match_date + timedelta(days=3),
        )
        submitted.action_submit()
        approved = self._expense(
            name="Approved company expense",
            amount=114,
            expense_date=self.match_date + timedelta(days=4),
        )
        approved.action_submit()
        approved._do_approve()
        submitted_bank = self._bank_line(
            amount=-113,
            bank_date=submitted.date,
            label="Submitted company expense",
        )
        approved_bank = self._bank_line(
            amount=-114,
            bank_date=approved.date,
            label="Approved company expense",
        )

        (submitted | approved)._usl_refresh_bank_match_candidates()
        submitted.usl_bank_match_candidate_ids.filtered(
            lambda candidate: (
                candidate.bank_statement_line_id == submitted_bank
            ),
        )._apply_match()
        approved.usl_bank_match_candidate_ids.filtered(
            lambda candidate: (
                candidate.bank_statement_line_id == approved_bank
            ),
        )._apply_match()

        submitted.invalidate_recordset(["state", "account_move_id"])
        approved.invalidate_recordset(["state", "account_move_id"])
        submitted_bank.invalidate_recordset(["is_reconciled"])
        approved_bank.invalidate_recordset(["is_reconciled"])
        self.assertEqual(submitted.state, "paid")
        self.assertEqual(approved.state, "paid")
        self.assertTrue(submitted.account_move_id.origin_payment_id)
        self.assertTrue(approved.account_move_id.origin_payment_id)
        self.assertTrue(submitted_bank.is_reconciled)
        self.assertTrue(approved_bank.is_reconciled)

    def test_duplicate_review_rolls_back_all_changes(self):
        existing = self._expense(
            name="Existing duplicate",
            amount=121,
            expense_date=self.match_date + timedelta(days=5),
        )
        existing.action_submit()
        existing._do_approve()
        expense = self._expense(
            name="Potential duplicate",
            amount=121,
            expense_date=existing.date,
        )
        bank_line = self._bank_line(
            amount=-121,
            bank_date=expense.date,
            label="Potential duplicate",
        )
        expense._usl_refresh_bank_match_candidates()
        candidate = expense.usl_bank_match_candidate_ids

        with self.assertRaisesRegex(UserError, "duplicate-expense review"):
            with self.env.cr.savepoint():
                candidate._apply_match()

        expense.invalidate_recordset([
            "state",
            "payment_mode",
            "account_move_id",
            "vendor_id",
        ])
        bank_line.invalidate_recordset(["is_reconciled"])
        candidate.invalidate_recordset(["state"])
        self.assertEqual(expense.state, "draft")
        self.assertEqual(expense.payment_mode, "own_account")
        self.assertFalse(expense.account_move_id)
        self.assertEqual(expense.vendor_id, self.vendor)
        self.assertFalse(bank_line.is_reconciled)
        self.assertEqual(candidate.state, "available")

    def test_bank_partner_change_is_disclosed_and_applied(self):
        expense = self._expense(
            name="Hotel booking",
            amount=123,
            expense_date=self.match_date + timedelta(days=6),
        )
        bank_partner = self.env["res.partner"].create({
            "name": "Correct Hotel Vendor",
        })
        bank_line = self._bank_line(
            amount=-123,
            bank_date=expense.date,
            label="Correct Hotel Vendor",
            partner=bank_partner,
        )
        expense._usl_refresh_bank_match_candidates()
        candidate = expense.usl_bank_match_candidate_ids

        action = candidate.action_open_confirmation()
        wizard = self.env[action["res_model"]].browse(action["res_id"])
        self.assertIn(self.vendor.display_name, wizard.confirmation_message)
        self.assertIn(bank_partner.display_name, wizard.confirmation_message)
        candidate._apply_match()

        expense.invalidate_recordset(["vendor_id"])
        bank_line.invalidate_recordset(["is_reconciled"])
        self.assertEqual(expense.vendor_id, bank_partner)
        self.assertTrue(bank_line.is_reconciled)

    def test_accepted_bank_line_invalidates_competing_suggestion(self):
        first = self._expense(name="Toronto first")
        second = self._expense(
            name="Toronto second",
            expense_date=self.match_date + timedelta(days=1),
        )
        self._bank_line(partner=self.vendor)
        first._usl_refresh_bank_match_candidates()
        second._usl_refresh_bank_match_candidates()
        first_candidate = first.usl_bank_match_candidate_ids
        second_candidate = second.usl_bank_match_candidate_ids

        first_candidate._apply_match()

        self.assertEqual(first_candidate.state, "accepted")
        self.assertEqual(second_candidate.state, "unavailable")
        self.assertIn("matched to", second_candidate.unavailable_reason)

    def test_posted_employee_expense_is_not_silently_converted(self):
        expense = self._expense()
        expense.action_submit()
        expense._do_approve()
        self.post_expenses_with_wizard(
            expense,
            journal=self.company_data["default_journal_purchase"],
            date=fields.Date.context_today(expense),
        )
        self.assertFalse(expense._usl_bank_match_is_eligible())
        self.assertIn(
            "posted as employee-paid",
            expense._usl_bank_match_ineligible_reason(),
        )
