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

    def test_company_payer_never_opens_employee_payment_review(self):
        expense = self._approved_for_posting(company_paid=True)
        bank = self._bank_line()
        self.assertFalse(expense.action_post())
        self.assertTrue(expense.account_move_id.origin_payment_id)
        self.assertFalse(bank.is_reconciled)

    def _posting_review(self, expenses):
        return self._review(expenses, "post")

    def test_post_continuation_keeps_employee_payment_and_bank_unmatched(self):
        expense = self._approved_for_posting()
        bank = self._bank_line()
        wizard = self._posting_review(expense)
        self.assertEqual(wizard.usl_review_candidate_ids.bank_statement_line_id, bank)
        self.assertFalse(expense.account_move_id)
        self._continue_review(wizard)
        self.assertEqual(expense.account_move_id.state, "posted")
        self.assertEqual(expense.account_move_id.move_type, "in_receipt")
        self.assertEqual(expense.payment_mode, "own_account")
        self.assertFalse(bank.is_reconciled)
        with self.assertRaisesRegex(UserError, "already complete"):
            wizard.action_continue_employee_payment()

    def test_no_match_posts_without_additional_confirmation(self):
        expense = self._approved_for_posting()
        self._bank_line(amount=-400)
        self._post_native_dialog(expense.action_post())
        self.assertEqual(expense.account_move_id.state, "posted")

    def test_near_match_requires_review_without_becoming_reconcilable(self):
        expense = self._approved_for_posting()
        self._bank_line(amount=-101)
        wizard = self._posting_review(expense)
        self.assertFalse(wizard.usl_review_candidate_ids.amount_is_exact)
        self._continue_review(wizard)
        self.assertTrue(expense.account_move_id)

    def test_changed_or_new_matches_require_fresh_confirmation(self):
        expense = self._approved_for_posting()
        bank = self._bank_line()
        wizard = self._posting_review(expense)
        original = wizard.usl_review_fingerprint
        bank.amount = -101
        action = wizard.action_continue_employee_payment()
        self.assertEqual(action["res_id"], wizard.id)
        self.assertTrue(wizard.usl_review_changed)
        self.assertNotEqual(wizard.usl_review_fingerprint, original)
        self.assertFalse(expense.account_move_id)
        second = self._bank_line(label="[QA] Newly received debit")
        wizard.action_continue_employee_payment()
        self.assertIn(second, wizard.usl_review_candidate_ids.bank_statement_line_id)
        self.assertFalse(expense.account_move_id)
        self._continue_review(wizard)
        self.assertTrue(expense.account_move_id)

    def test_bulk_confirmation_is_before_any_member_posts(self):
        first = self._approved_for_posting("[QA] Matched batch member")
        second = self._approved_for_posting("[QA] Unmatched batch member", amount=300)
        self._bank_line()
        wizard = self._posting_review(first | second)
        self.assertFalse((first | second).account_move_id)
        self.assertEqual(wizard.usl_review_expense_ids, first | second)
        self._continue_review(wizard)
        # Native employee receipts group these two expenses by employee
        # (hr_expense._prepare_receipts_vals); both expense lines must survive.
        self.assertEqual(first.account_move_id.state, "posted")
        self.assertEqual(second.account_move_id.state, "posted")
        self.assertEqual((first | second).account_move_id.line_ids.expense_id, first | second)
        self.assertEqual(sum((first | second).account_move_id.mapped("amount_total")), 400)

    def test_employee_confirmation_posts_native_receipt_once(self):
        expense = self._approved_for_posting("[QA] Employee payment decision", company_paid=False)
        bank = self._bank_line()
        wizard = self._posting_review(expense)
        wizard.write({"employee_journal_id": self.company_data["default_journal_purchase"].id})
        self._continue_review(wizard)
        self.assertEqual(expense.account_move_id.state, "posted")
        self.assertEqual(expense.account_move_id.move_type, "in_receipt")
        self.assertFalse(expense.account_move_id.origin_payment_id)
        self.assertEqual(expense.payment_mode, "own_account")
        self.assertFalse(bank.is_reconciled)

    def test_employee_final_post_rechecks_new_bank_data(self):
        expense = self._approved_for_posting()
        action = expense.action_post()
        wizard = self.env[action["res_model"]].browse(action["res_id"]).with_context(action["context"])
        wizard.employee_journal_id = self.company_data["default_journal_purchase"]
        bank = self._bank_line()
        action = wizard.action_post_entry()
        self.assertEqual(action["res_id"], wizard.id)
        self.assertEqual(wizard.usl_review_stage, "post_entry")
        self.assertIn(bank, wizard.usl_review_candidate_ids.bank_statement_line_id)
        self.assertFalse(expense.account_move_id)
        wizard.action_continue_employee_payment()
        self.assertEqual(expense.account_move_id.state, "posted")

    def test_boolean_context_cannot_accidentally_skip_confirmation(self):
        expense = self._approved_for_posting("[QA] RPC confirmation is explicit")
        self._bank_line()
        action = expense.with_context(usl_bank_match_transition=True).action_post()
        self.assertFalse(expense.account_move_id)
        self.assertEqual(action["res_model"], "hr.expense.post.wizard")

    def test_post_review_does_not_elevate_readonly_accountant(self):
        expense = self._approved_for_posting()
        self._bank_line()
        with self.assertRaises(AccessError):
            action = expense.with_user(self.readonly_user).action_post()
            wizard = self.env[action["res_model"]].with_user(self.readonly_user).browse(action["res_id"]).with_context(action["context"])
            wizard.action_post_entry()
        self.assertFalse(expense.account_move_id)

    def test_review_evidence_uses_only_the_expense_company(self):
        expense = self._approved_for_posting()
        other = self.company_data_2
        statement = self.env["account.bank.statement"].with_company(other["company"]).create({
            "journal_id": other["default_journal_bank"].id, "date": self.match_date,
            "name": "[QA] Other-company statement"})
        self.env["account.bank.statement.line"].with_company(other["company"]).create({
            "journal_id": other["default_journal_bank"].id, "statement_id": statement.id,
            "date": self.match_date, "amount": -100, "payment_ref": "[QA] Hidden other-company match"})
        self.assertFalse(expense._usl_employee_payment_review("post"))
        self.assertFalse(expense.usl_bank_match_candidate_ids)
        self._post_native_dialog(expense.action_post())
        self.assertEqual(expense.account_move_id.company_id, self.env.company)

    def test_foreign_currency_suggestion_requires_posting_review(self):
        expense = self._expense(name="[QA] Foreign payment", amount=1000, currency=self.other_currency)
        expense._do_approve()
        bank = self._bank_line(amount=-40, foreign_currency=self.other_currency, amount_currency=-1000)
        wizard = self._posting_review(expense)
        self.assertEqual(wizard.usl_review_candidate_ids.bank_statement_line_id, bank)
        self.assertTrue(wizard.usl_review_candidate_ids.amount_is_exact)
        self.assertFalse(expense.account_move_id)

    def test_mixed_bulk_posts_company_payment_and_employee_receipt_after_one_review(self):
        company = self._approved_for_posting(company_paid=True)
        employee = self._approved_for_posting(amount=300)
        bank = self._bank_line(amount=-300)
        wizard = self._posting_review(company | employee)
        self.assertFalse((company | employee).account_move_id)
        self._continue_review(wizard)
        self.assertEqual(len((company | employee).account_move_id), 2)
        self.assertTrue(company.account_move_id.origin_payment_id)
        self.assertEqual(employee.account_move_id.move_type, "in_receipt")
        self.assertFalse(bank.is_reconciled)

    def test_direct_employee_post_entry_requires_review_and_rejects_repetition(self):
        expense = self._approved_for_posting("[QA] Direct receipt entry", company_paid=False)
        self._bank_line()
        wizard = self.env["hr.expense.post.wizard"].with_context(active_ids=expense.ids).create({
            "employee_journal_id": self.company_data["default_journal_purchase"].id,
        })
        action = wizard.action_post_entry()
        self.assertEqual(action["res_id"], wizard.id)
        self.assertFalse(expense.account_move_id)
        self._continue_review(wizard)
        with self.assertRaisesRegex(UserError, "approved expense"):
            wizard.action_post_entry()

    def test_disappearing_matches_post_without_empty_review(self):
        expense = self._approved_for_posting()
        bank = self._bank_line()
        wizard = self._posting_review(expense)
        bank.amount = -400
        self._continue_review(wizard)
        self.assertEqual(expense.account_move_id.state, "posted")
        self.assertFalse(bank.is_reconciled)
        self.assertFalse(expense.usl_bank_match_available_count)

    def test_open_and_cancel_review_preserves_financial_state(self):
        expense = self._approved_for_posting("[QA] Cancel payment review")
        bank = self._bank_line()
        models = ("account.move", "account.move.line", "account.payment", "account.partial.reconcile", "account.full.reconcile")
        before = {model: self.env[model].search_count([]) for model in models}
        expense_before = expense.read(["state", "payment_mode", "total_amount_currency", "account_id", "tax_ids", "analytic_distribution", "vendor_id", "account_move_id", "write_date"])
        bank_before = bank.read(["amount", "amount_currency", "is_reconciled", "reconcile_data", "write_date"])
        wizard = self._posting_review(expense)
        wizard.unlink()  # Native Cancel discards only the temporary dialog.
        self.assertEqual({model: self.env[model].search_count([]) for model in models}, before)
        self.assertEqual(expense.read(list(expense_before[0].keys())), expense_before)
        self.assertEqual(bank.read(list(bank_before[0].keys())), bank_before)

    def test_changed_bank_label_refreshes_the_confirmation_evidence(self):
        expense = self._approved_for_posting("[QA] Changed bank label")
        bank = self._bank_line()
        wizard = self._posting_review(expense)
        bank.payment_ref = "[QA] Corrected bank transaction label"
        self._continue_review(wizard)
        self.assertTrue(wizard.usl_review_changed)
        self.assertIn(bank, wizard.usl_review_candidate_ids.bank_statement_line_id)
        self.assertFalse(expense.account_move_id)

    def test_final_review_redispatches_current_company_payment_mode(self):
        expense = self._approved_for_posting("[QA] Payer changes during final review", company_paid=False)
        action = expense.action_post()
        wizard = self.env[action["res_model"]].browse(action["res_id"]).with_context(action["context"])
        self._bank_line()
        wizard.action_post_entry()
        expense.write({"payment_mode": "company_account", "payment_method_line_id": self.outbound_method.id})
        self._continue_review(wizard)
        self.assertTrue(expense.account_move_id.origin_payment_id)
        self.assertEqual(expense.account_move_id.move_type, "entry")

    def test_final_review_redispatches_current_mixed_payment_membership(self):
        first = self._approved_for_posting("[QA] Payer becomes company", company_paid=False)
        second = self._approved_for_posting("[QA] Payer remains employee", company_paid=False, amount=300)
        action = (first | second).action_post()
        wizard = self.env[action["res_model"]].browse(action["res_id"]).with_context(action["context"])
        journal = self.company_data["default_journal_purchase"]
        wizard.write({"employee_journal_id": journal.id, "accounting_date": self.match_date + timedelta(days=1)})
        self._bank_line()
        wizard.action_post_entry()
        first.write({"payment_mode": "company_account", "payment_method_line_id": self.outbound_method.id})
        self._continue_review(wizard)
        self.assertTrue(first.account_move_id.origin_payment_id)
        self.assertEqual(second.account_move_id.move_type, "in_receipt")
        self.assertNotEqual(first.account_move_id, second.account_move_id)
        self.assertEqual(second.account_move_id.journal_id, journal)
        self.assertEqual(second.account_move_id.invoice_date, self.match_date + timedelta(days=1))

    def _approved_for_posting(self, name="[QA] Employee expense", *, company_paid=False, amount=100):
        expense = self._expense(name=name, amount=amount)
        if company_paid:
            expense.write({"payment_mode": "company_account", "payment_method_line_id": self.outbound_method.id})
        expense._do_approve()
        return expense

    def _review(self, expenses, stage):
        action = getattr(expenses, "action_" + stage)()
        self.assertEqual(action["res_model"], "hr.expense.post.wizard")
        wizard = self.env[action["res_model"]].browse(action["res_id"]).with_context(action["context"])
        self.assertTrue(wizard.usl_review_candidate_ids)
        self.assertEqual(wizard.usl_review_stage, stage)
        return wizard

    def _post_native_dialog(self, action, *, accounting_date=None):
        wizard = self.env[action["res_model"]].browse(action["res_id"]).with_context(action["context"])
        wizard.write({"employee_journal_id": self.company_data["default_journal_purchase"].id,
                      "accounting_date": accounting_date or self.match_date})
        return wizard, wizard.action_post_entry()

    def _continue_review(self, wizard):
        action = wizard.action_continue_employee_payment()
        if action.get("res_model") == "hr.expense.post.wizard" and action["res_id"] != wizard.id:
            native = self.env[action["res_model"]].browse(action["res_id"]).with_context(action["context"])
            native.write({"employee_journal_id": wizard.employee_journal_id.id,
                          "accounting_date": wizard.accounting_date})
            return native.action_post_entry()
        return action

    def test_employee_submit_opens_existing_candidates_before_transition(self):
        expense = self._expense(name="[QA] Employee submit candidates")
        bank = self._bank_line()
        action = expense.action_submit()
        self.assertIsInstance(action, dict, "Submit must offer existing bank candidates before advancing")
        self.assertEqual(expense.state, "draft")
        wizard = self.env[action["res_model"]].browse(action["res_id"])
        self.assertEqual(wizard.usl_review_candidate_ids.bank_statement_line_id, bank)
        self.assertEqual(wizard.usl_review_stage, "submit")
        candidate_ids = expense.usl_bank_match_candidate_ids.ids
        wizard.action_continue_employee_payment()
        self.assertEqual(expense.state, "submitted")
        self.assertEqual(expense.usl_bank_match_candidate_ids.ids, candidate_ids)
        self.assertFalse(expense.account_move_id)
        self.assertFalse(bank.is_reconciled)
        with self.assertRaisesRegex(UserError, "already complete"):
            wizard.action_continue_employee_payment()

    def test_approve_continues_only_original_transition(self):
        expense = self._expense()
        expense.action_submit()
        self._bank_line()
        wizard = self._review(expense, "approve")
        self.assertEqual(expense.state, "submitted")
        wizard.action_continue_employee_payment()
        self.assertEqual(expense.state, "approved")
        self.assertFalse(expense.account_move_id)

    def test_post_continue_retains_native_controls_and_does_not_prompt_twice(self):
        expense = self._approved_for_posting()
        bank = self._bank_line()
        review = self._review(expense, "post")
        action = review.action_continue_employee_payment()
        native, result = self._post_native_dialog(action, accounting_date=self.match_date + timedelta(days=1))
        self.assertEqual(result["res_model"], "account.move")
        self.assertEqual(expense.account_move_id.state, "posted")
        self.assertEqual(expense.account_move_id.move_type, "in_receipt")
        self.assertEqual(expense.account_move_id.journal_id, native.employee_journal_id)
        self.assertEqual(expense.account_move_id.invoice_date, native.accounting_date)
        self.assertFalse(bank.is_reconciled)
        with self.assertRaisesRegex(UserError, "approved expense"):
            native.action_post_entry()

    def test_no_candidates_uses_native_submit_approve_and_post(self):
        expense = self._expense()
        self._bank_line(amount=-400)
        self.assertFalse(expense.action_submit())
        self.assertEqual(expense.state, "submitted")
        self.assertFalse(expense.action_approve())
        self.assertEqual(expense.state, "approved")
        action = expense.action_post()
        native, result = self._post_native_dialog(action)
        self.assertFalse(native.usl_review_candidate_ids)
        self.assertEqual(result["res_model"], "account.move")

    def test_company_paid_continues_without_employee_prompt(self):
        expense = self._approved_for_posting(company_paid=True)
        self._bank_line()
        self.assertFalse(expense.action_post())
        self.assertEqual(expense.account_move_id.state, "posted")
        self.assertFalse(expense.usl_bank_match_candidate_ids)

    def test_near_match_reuses_existing_unusable_candidate(self):
        expense = self._expense()
        self._bank_line(amount=-101)
        wizard = self._review(expense, "submit")
        self.assertFalse(wizard.usl_review_candidate_ids.amount_is_exact)
        with self.assertRaisesRegex(UserError, "close but not equal"):
            wizard.usl_review_candidate_ids.action_open_confirmation()
        wizard.action_continue_employee_payment()
        self.assertEqual(expense.state, "submitted")

    def test_changed_matches_refresh_once_before_continuation(self):
        expense = self._expense()
        bank = self._bank_line()
        wizard = self._review(expense, "submit")
        bank.payment_ref = "[QA] Revised company debit"
        action = wizard.action_continue_employee_payment()
        self.assertEqual(action["res_id"], wizard.id)
        self.assertTrue(wizard.usl_review_changed)
        self.assertEqual(wizard.usl_review_candidate_ids.bank_label, bank.payment_ref)
        self.assertEqual(expense.state, "draft")
        wizard.action_continue_employee_payment()
        self.assertEqual(expense.state, "submitted")

    def test_disappearing_matches_continue_without_empty_prompt(self):
        expense = self._expense()
        bank = self._bank_line()
        wizard = self._review(expense, "submit")
        bank.amount = -400
        result = wizard.action_continue_employee_payment()
        self.assertNotEqual(result.get("res_model"), "hr.expense.post.wizard")
        self.assertEqual(expense.state, "submitted")

    def test_cancel_only_refreshes_candidate_cache(self):
        expense = self._expense()
        bank = self._bank_line()
        models = ("account.move", "account.move.line", "account.payment", "account.partial.reconcile", "account.full.reconcile")
        before = {model: self.env[model].search_count([]) for model in models}
        wizard = self._review(expense, "submit")
        wizard.unlink()
        self.assertEqual(expense.state, "draft")
        self.assertEqual(expense.payment_mode, "own_account")
        self.assertFalse(bank.is_reconciled)
        self.assertEqual({model: self.env[model].search_count([]) for model in models}, before)

    def test_new_bank_data_at_native_final_post_preserves_date(self):
        expense = self._approved_for_posting()
        action = expense.action_post()
        native = self.env[action["res_model"]].browse(action["res_id"]).with_context(action["context"])
        native.write({"employee_journal_id": self.company_data["default_journal_purchase"].id, "accounting_date": self.match_date})
        self._bank_line()
        review = native.action_post_entry()
        self.assertEqual(review["res_id"], native.id)
        self.assertEqual(native.usl_review_stage, "post_entry")
        native.action_continue_employee_payment()
        self.assertEqual(expense.account_move_id.invoice_date, self.match_date)
        self.assertEqual(expense.account_move_id.move_type, "in_receipt")

    def test_batch_checks_before_company_payment_and_retains_selection(self):
        company = self._approved_for_posting(company_paid=True, amount=300)
        employee = self._approved_for_posting()
        bank = self._bank_line()
        wizard = self._review(company | employee, "post")
        self.assertEqual(wizard.usl_review_expense_ids, company | employee)
        self.assertFalse((company | employee).account_move_id)
        action = wizard.action_continue_employee_payment()
        self._post_native_dialog(action)
        self.assertTrue(company.account_move_id.origin_payment_id)
        self.assertEqual(employee.account_move_id.move_type, "in_receipt")
        self.assertFalse(bank.is_reconciled)

    def test_existing_use_completes_match_and_continues_pending_batch(self):
        first = self._expense()
        second = self._expense(name="[QA] Other expense", amount=300)
        bank = self._bank_line()
        review = self._review(first | second, "submit")
        action = review.usl_review_candidate_ids.with_context(review.env.context).action_open_confirmation()
        match = self.env[action["res_model"]].browse(action["res_id"]).with_context(review.env.context)
        match.action_confirm()
        self.assertEqual(first.payment_mode, "company_account")
        self.assertEqual(first.account_move_id.state, "posted")
        self.assertTrue(bank.is_reconciled)
        self.assertEqual(second.state, "submitted")
        self.assertFalse(second.account_move_id)

    def test_mixed_same_amount_rechecks_changed_competition_after_company_post(self):
        company = self._approved_for_posting(company_paid=True)
        employee = self._approved_for_posting()
        self._bank_line()
        review = self._review(company | employee, "post")
        self.assertEqual(review.usl_review_candidate_ids.competing_expense_count, 1)
        action = review.action_continue_employee_payment()
        native = self.env[action["res_model"]].browse(action["res_id"]).with_context(action["context"])
        native.employee_journal_id = self.company_data["default_journal_purchase"]
        self.assertEqual(company.account_move_id.state, "posted")
        self.assertFalse(employee.account_move_id)
        refreshed = native.action_post_entry()
        self.assertEqual(refreshed["res_model"], "hr.expense.post.wizard")
        self.assertEqual(refreshed["res_id"], native.id)
        self.assertTrue(native.usl_review_changed)
        self.assertEqual(native.usl_review_candidate_ids.competing_expense_count, 0)
        self.assertFalse(employee.account_move_id)
        native.action_continue_employee_payment()
        self.assertEqual(employee.account_move_id.state, "posted")

    def test_users_without_bank_authority_keep_native_submit_approve(self):
        expense = self._expense()
        self._bank_line()
        expense.with_user(self.expense_user_employee).action_submit()
        self.assertEqual(expense.state, "submitted")
        expense.with_user(self.expense_user_manager).action_approve()
        self.assertEqual(expense.state, "approved")
        self.assertFalse(expense.usl_bank_match_candidate_ids)
        with self.assertRaises(AccessError):
            expense.with_user(self.expense_user_employee).action_refresh_bank_match_candidates()

    def test_final_entry_payer_change_rechecks_competition_and_keeps_controls(self):
        first = self._approved_for_posting()
        second = self._approved_for_posting()
        action = (first | second).action_post()
        native = self.env[action["res_model"]].browse(action["res_id"]).with_context(action["context"])
        journal = self.company_data["default_journal_purchase"]
        native.write({"employee_journal_id": journal.id, "accounting_date": self.match_date})
        self._bank_line()
        native.action_post_entry()
        first.write({"payment_mode": "company_account", "payment_method_line_id": self.outbound_method.id})
        native.action_continue_employee_payment()  # Show the changed payer set.
        action = native.action_continue_employee_payment()
        current = self.env[action["res_model"]].browse(action["res_id"]).with_context(action["context"])
        self.assertEqual(first.account_move_id.state, "posted")
        self.assertFalse(second.account_move_id)
        self.assertTrue(current.usl_review_changed)
        self.assertEqual(current.usl_review_candidate_ids.competing_expense_count, 0)
        self.assertEqual(current.employee_journal_id, journal)
        self.assertEqual(current.accounting_date, self.match_date)
        current.action_continue_employee_payment()
        self.assertEqual(second.account_move_id.state, "posted")
        self.assertEqual(second.account_move_id.invoice_date, self.match_date)

    def test_posting_user_without_matching_rights_keeps_native_receipt(self):
        expense = self._approved_for_posting()
        bank = self._bank_line()
        user = self.expense_user_manager
        user.group_ids |= self.env.ref("account.group_account_invoice")
        self.assertFalse(user.has_group("account.group_account_manager"))
        action = expense.with_user(user).action_post()
        wizard = self.env[action["res_model"]].with_user(user).browse(action["res_id"]).with_context(action["context"])
        self.assertFalse(wizard.usl_review_candidate_ids)
        wizard.employee_journal_id = self.company_data["default_journal_purchase"]
        wizard.action_post_entry()
        self.assertEqual(expense.account_move_id.state, "posted")
        self.assertEqual(expense.account_move_id.create_uid, user)
        self.assertFalse(expense.usl_bank_match_candidate_ids)
        self.assertFalse(bank.is_reconciled)

    def test_boolean_context_cannot_skip_candidates(self):
        expense = self._expense()
        self._bank_line()
        action = expense.with_context(usl_bank_match_transition=True).action_submit()
        self.assertEqual(action["res_model"], "hr.expense.post.wizard")
        self.assertEqual(expense.state, "draft")

    def test_state_changes_refuse_stale_continuation(self):
        expense = self._expense()
        self._bank_line()
        wizard = self._review(expense, "submit")
        expense._do_approve()
        with self.assertRaisesRegex(UserError, "state changed"):
            wizard.action_continue_employee_payment()
        self.assertFalse(expense.account_move_id)

    def test_changed_payer_at_final_entry_uses_current_native_dispatch(self):
        expense = self._approved_for_posting()
        action = expense.action_post()
        native = self.env[action["res_model"]].browse(action["res_id"]).with_context(action["context"])
        self._bank_line()
        native.action_post_entry()
        expense.write({"payment_mode": "company_account", "payment_method_line_id": self.outbound_method.id})
        native.action_continue_employee_payment()
        self.assertTrue(expense.account_move_id.origin_payment_id)
        self.assertEqual(expense.account_move_id.move_type, "entry")

    def test_other_company_bank_evidence_stays_hidden(self):
        expense = self._expense()
        other = self.company_data_2
        statement = self.env["account.bank.statement"].with_company(other["company"]).create({
            "journal_id": other["default_journal_bank"].id, "date": self.match_date, "name": "[QA] Other-company statement"})
        self.env["account.bank.statement.line"].with_company(other["company"]).create({
            "journal_id": other["default_journal_bank"].id, "statement_id": statement.id,
            "date": self.match_date, "amount": -100, "payment_ref": "[QA] Other-company debit"})
        self.assertFalse(expense.action_submit())
        self.assertEqual(expense.state, "submitted")
        self.assertFalse(expense.usl_bank_match_candidate_ids)
