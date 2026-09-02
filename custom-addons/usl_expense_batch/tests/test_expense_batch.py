from odoo import Command, fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import Form, tagged
from odoo.tools import format_date

from odoo.addons.hr_expense.tests.common import TestExpenseCommon
from odoo.addons.mail.tests.common import mail_new_test_user


@tagged("post_install", "-at_install", "usl_expense_batch")
class TestExpenseBatch(TestExpenseCommon):
    def _expense(
        self,
        name,
        *,
        amount=42.0,
        employee=None,
        payment_mode="own_account",
        with_receipt=True,
    ):
        employee = employee or self.expense_employee
        expense = self.env["hr.expense"].with_user(
            employee.user_id or self.env.user,
        ).create({
            "name": name,
            "date": fields.Date.from_string("2026-07-10"),
            "employee_id": employee.id,
            "product_id": self.product_c.id,
            "company_id": self.env.company.id,
            "payment_mode": payment_mode,
            "total_amount_currency": amount,
            "analytic_distribution": {str(self.analytic_account_1.id): 100},
            "analytic_context_source": "product",
        })
        if with_receipt:
            attachment = self.env["ir.attachment"].sudo().create({
                "name": f"{name}.pdf",
                "type": "binary",
                "raw": b"expense batch test receipt",
                "res_model": "hr.expense",
                "res_id": expense.id,
            })
            expense.sudo().message_main_attachment_id = attachment
            expense.invalidate_recordset([
                "message_main_attachment_id",
                "batch_attachment_status",
                "batch_readiness",
                "batch_incomplete_reason",
            ])
        return expense

    def _batch(self, expenses, name="Toronto trip — July 2026"):
        return self.env["usl.expense.batch"].with_user(
            self.expense_user_employee,
        ).create({
            "name": name,
            "purpose": "Customer workshops in Toronto",
            "employee_id": self.expense_employee.id,
            "company_id": self.env.company.id,
            "expense_ids": [Command.set(expenses.ids)],
        })

    def test_expense_and_batch_expose_canonical_document_context(self):
        expense = self._expense("Toronto hotel")
        attachment = expense.message_main_attachment_id
        expense_context = expense._document_archive_context(attachment)

        self.assertEqual(expense_context["document_type"], "Expense receipt")
        self.assertEqual(expense_context["tags"], ["Accounting", "Expenses"])
        self.assertEqual(expense_context["document_date"], "2026-07-10")
        self.assertEqual(expense_context["archive_mode"], "mandatory")
        self.assertEqual(expense_context["document_role"], "evidence")

        batch = self._batch(expense)
        batch_context = batch._document_archive_context()
        self.assertEqual(
            batch_context["document_type"],
            "Expense batch evidence",
        )
        self.assertEqual(batch_context["tags"], ["Accounting", "Expenses"])
        self.assertEqual(batch_context["archive_mode"], "mandatory")
        self.assertEqual(batch_context["document_role"], "evidence")
        self.assertIn("usl.expense.batch", self.env["usl.document.link"]._allowed_models())

    def test_readiness_and_preview_wizard_are_deterministic(self):
        complete = self._expense("Toronto hotel")
        incomplete = self._expense("Toronto taxi", with_receipt=False)
        self.assertEqual(complete.batch_readiness, "ready")
        self.assertEqual(incomplete.batch_readiness, "incomplete")
        self.assertEqual(complete.batch_attachment_status, "attached")
        self.assertEqual(incomplete.batch_attachment_status, "missing")
        self.assertIn("receipt", incomplete.batch_incomplete_reason)

        with self.assertRaisesRegex(UserError, "Select at least one eligible expense"):
            (
                self.env["hr.expense"]
                .with_user(self.expense_user_employee)
                .action_open_expense_batch_wizard()
            )

        action = (
            self.env["hr.expense"]
            .with_user(self.expense_user_employee)
            .action_open_expense_batch_wizard(complete.ids)
        )
        wizard = self.env[action["res_model"]].browse(action["res_id"])
        self.assertIn("analytic_precision", wizard._fields)
        self.assertIn(
            "analytic_precision",
            wizard.read(["analytic_precision"])[0],
        )
        self.assertEqual(action["views"], [(False, "form")])
        self.assertEqual(wizard.expense_ids, complete)
        self.assertEqual(wizard.expense_count, 1)
        self.assertEqual(wizard.total_amount, complete.total_amount)
        self.assertEqual(wizard.employee_paid_total, complete.total_amount)
        self.assertEqual(wizard.company_paid_total, 0)
        self.assertEqual(
            wizard.main_analytic_activity,
            self.analytic_account_1.display_name,
        )
        self.assertIn(self.expense_employee.name, wizard.name)
        self.assertTrue(wizard.purpose)

        complete.sudo().approval_state = "approved"
        self.assertEqual(complete.batch_readiness, "ready")
        complete.sudo().approval_state = False

        preview = self.env["usl.expense.batch.create.wizard"].create({
            "expense_ids": [Command.set((complete + incomplete).ids)],
        })
        self.assertEqual(preview.incomplete_expense_ids, incomplete)
        self.assertEqual(preview.incomplete_count, 1)
        self.assertEqual(preview.readiness_state, "incomplete")

        batch = self._batch(complete + incomplete)
        self.assertEqual(batch.incomplete_expense_ids, incomplete)
        self.assertEqual(batch.incomplete_count, 1)
        self.assertEqual(batch.readiness_state, "incomplete")

    def test_mixed_draft_and_approved_expenses_advance_without_regression(self):
        draft = self._expense("Toronto draft")
        approved = self._expense(
            "Toronto approved",
            amount=43,
            with_receipt=False,
        )
        approved.sudo().approval_state = "approved"
        self.assertEqual(approved.state, "approved")
        self.assertEqual(approved.batch_readiness, "incomplete")

        action = (
            self.env["hr.expense"]
            .with_user(self.expense_user_employee)
            .action_open_expense_batch_wizard((draft + approved).ids)
        )
        wizard = (
            self.env[action["res_model"]]
            .with_user(self.expense_user_employee)
            .browse(action["res_id"])
        )
        self.assertEqual(wizard.expense_ids, draft + approved)
        self.assertEqual(wizard.draft_count, 1)
        self.assertEqual(wizard.draft_incomplete_count, 0)
        self.assertEqual(wizard.readiness_state, "incomplete")

        batch = wizard._create_batch()
        self.assertEqual(batch.expense_progress, "draft")
        batch.with_user(self.expense_user_employee).action_submit()
        self.assertEqual(draft.state, "submitted")
        self.assertEqual(approved.state, "approved")
        self.assertEqual(batch.expense_progress, "submitted")

        batch.with_user(self.expense_user_manager).action_approve()
        self.assertEqual(draft.state, "approved")
        self.assertEqual(approved.state, "approved")
        self.assertEqual(batch.expense_progress, "approved")

    def test_wizard_actions_create_then_confirm_and_close_the_modal(self):
        create_expense = self._expense("Toronto create and close")
        create_wizard = self.env[
            "usl.expense.batch.create.wizard"
        ].with_user(self.expense_user_employee).create({
            "expense_ids": [Command.set(create_expense.ids)],
        })
        create_action = create_wizard.action_create_batch()
        self.assertEqual(create_action["tag"], "display_notification")
        self.assertEqual(
            create_action["params"]["next"],
            {"type": "ir.actions.act_window_close"},
        )
        self.assertTrue(create_expense.expense_batch_id)
        self.assertEqual(create_expense.state, "draft")

        submit_expense = self._expense("Toronto submit and close")
        submit_wizard = self.env[
            "usl.expense.batch.create.wizard"
        ].with_user(self.expense_user_employee).create({
            "expense_ids": [Command.set(submit_expense.ids)],
        })
        submit_action = submit_wizard.action_create_and_submit()
        self.assertEqual(submit_action["tag"], "display_notification")
        self.assertEqual(
            submit_action["params"]["next"],
            {"type": "ir.actions.act_window_close"},
        )
        self.assertTrue(submit_expense.expense_batch_id)
        self.assertEqual(submit_expense.state, "submitted")

    def test_submitted_and_already_batched_expenses_are_rejected(self):
        submitted = self._expense("Toronto submitted")
        submitted.sudo().approval_state = "submitted"
        self.assertEqual(submitted.state, "submitted")

        with self.assertRaisesRegex(
            UserError,
            "Only unbatched draft, approved, or posted expenses",
        ):
            self.env["hr.expense"].action_open_expense_batch_wizard(
                submitted.ids,
            )
        with self.assertRaisesRegex(
            ValidationError,
            "Only unbatched draft, approved, or posted expenses",
        ), self.cr.savepoint():
            self.env["usl.expense.batch"].sudo().create({
                "name": "Invalid submitted claim",
                "purpose": "Must not be batched",
                "employee_id": self.expense_employee.id,
                "company_id": self.env.company.id,
                "expense_ids": [Command.set(submitted.ids)],
            })

        first = self._expense("Toronto first batch")
        self._batch(first)
        with self.assertRaisesRegex(
            UserError,
            "Only unbatched draft, approved, or posted expenses",
        ):
            self.env["hr.expense"].action_open_expense_batch_wizard(first.ids)

    def test_posted_batch_stays_open_and_accepts_later_expenses(self):
        posted = self._expense("Toronto posted")
        posted.sudo().approval_state = "approved"
        self.post_expenses_with_wizard(posted.with_user(self.env.user))
        self.assertEqual(posted.state, "posted")
        self.assertTrue(posted.account_move_id)
        self.assertFalse(posted.account_move_id.expense_batch_id)

        action = (
            self.env["hr.expense"]
            .with_user(self.expense_user_employee)
            .action_open_expense_batch_wizard(posted.ids)
        )
        wizard = (
            self.env[action["res_model"]]
            .with_user(self.expense_user_employee)
            .browse(action["res_id"])
        )
        self.assertEqual(wizard.expense_ids, posted)
        self.assertEqual(wizard.draft_count, 0)
        batch = wizard._create_batch()

        self.assertEqual(batch.expense_progress, "posted")
        self.assertTrue(batch.active)
        self.assertEqual(posted.expense_batch_id, batch)
        self.assertEqual(posted.account_move_id.expense_batch_id, batch)

        later_posted = self._expense("Toronto later posted", amount=48)
        later_posted.sudo().approval_state = "approved"
        self.post_expenses_with_wizard(later_posted.with_user(self.env.user))
        later_move = later_posted.account_move_id
        ledger_before = [
            (line.id, line.debit, line.credit, line.amount_currency)
            for line in later_move.line_ids.sorted("id")
        ]
        draft = self._expense("Toronto late draft", amount=49)
        self.assertIn(
            batch.id,
            [
                candidate["id"]
                for candidate in self.env[
                    "hr.expense"
                ].get_expense_batch_candidates((later_posted + draft).ids)
            ],
        )

        result = batch.with_user(self.expense_user_employee).add_expenses(
            (later_posted + draft).ids,
        )
        repeated = batch.with_user(self.expense_user_employee).add_expenses(
            (later_posted + draft).ids,
        )

        self.assertEqual(result["added"], 2)
        self.assertEqual(repeated["added"], 0)
        self.assertEqual(repeated["unchanged"], 2)
        self.assertEqual(later_posted.expense_batch_id, batch)
        self.assertEqual(later_move.expense_batch_id, batch)
        self.assertEqual(later_move.state, "posted")
        self.assertEqual(
            [
                (line.id, line.debit, line.credit, line.amount_currency)
                for line in later_move.line_ids.sorted("id")
            ],
            ledger_before,
        )
        self.assertEqual(draft.expense_batch_id, batch)
        self.assertEqual(batch.expense_progress, "draft")
        self.assertTrue(batch.active)

    def test_posted_batch_context_remains_editable_without_rewriting_accounting(self):
        posted = self._expense("Toronto immutable accounting", amount=84)
        posted.sudo().approval_state = "approved"
        self.post_expenses_with_wizard(posted.with_user(self.env.user))
        batch = self._batch(posted)
        move = posted.account_move_id
        expense_before = {
            "account_id": posted.account_id.id,
            "analytic_distribution": posted.analytic_distribution,
            "total_amount": posted.total_amount,
            "state": posted.state,
        }
        ledger_before = [
            (line.id, line.debit, line.credit, line.amount_currency)
            for line in move.line_ids.sorted("id")
        ]
        revision_before = batch.context_revision

        batch.with_user(self.expense_user_manager).write({
            "name": "Toronto retrospective evidence",
            "purpose": "Updated documentary context",
            "context_type": "project",
            "context_date_from": fields.Date.from_string("2026-07-01"),
            "context_date_to": fields.Date.from_string("2026-07-31"),
            "notes": "<p>Reviewed after posting.</p>",
            "account_override_id": self.expense_account.id,
            "analytic_distribution": {str(self.analytic_account_2.id): 100},
        })

        self.assertEqual(batch.context_revision, revision_before + 1)
        self.assertEqual(batch.purpose, "Updated documentary context")
        self.assertEqual(batch.account_override_id, self.expense_account)
        self.assertEqual(batch.preview_context_application()["skipped"], 1)
        self.assertEqual(batch.apply_context()["applied"], 0)
        self.assertEqual(
            {
                "account_id": posted.account_id.id,
                "analytic_distribution": posted.analytic_distribution,
                "total_amount": posted.total_amount,
                "state": posted.state,
            },
            expense_before,
        )
        self.assertEqual(
            [
                (line.id, line.debit, line.credit, line.amount_currency)
                for line in move.line_ids.sorted("id")
            ],
            ledger_before,
        )

    def test_approved_and_posted_expenses_can_join_existing_batch(self):
        existing = self.env["usl.expense.batch"].with_user(
            self.expense_user_employee,
        ).create({
            "name": "Toronto retrospective review",
            "purpose": "Group later-stage expenses without rewriting accounting",
            "employee_id": self.expense_employee.id,
            "company_id": self.env.company.id,
        })
        approved = self._expense("Toronto approved retrospective")
        approved.sudo().approval_state = "approved"
        posted = self._expense("Toronto posted retrospective", amount=44)
        posted.sudo().approval_state = "approved"
        self.post_expenses_with_wizard(posted.with_user(self.env.user))

        wizard = self.env[
            "usl.expense.batch.create.wizard"
        ].with_user(self.expense_user_employee).create({
            "mode": "existing",
            "batch_id": existing.id,
            "expense_ids": [Command.set((approved + posted).ids)],
        })
        self.assertEqual(wizard._create_batch(), existing)

        self.assertEqual(approved.expense_batch_id, existing)
        self.assertEqual(posted.expense_batch_id, existing)
        self.assertEqual(approved.state, "approved")
        self.assertEqual(posted.state, "posted")
        self.assertEqual(posted.account_move_id.expense_batch_id, existing)

    def test_add_expenses_service_enforces_archive_and_readonly_access(self):
        candidate = self._expense("Toronto controlled grouping")
        target = self._batch(self._expense("Toronto submitted grouping"))
        target.with_user(self.expense_user_employee).action_submit()
        target.active = False

        with self.assertRaisesRegex(
            UserError,
            "Reopen this Expense Batch",
        ):
            target.with_user(self.expense_user_employee).add_expenses(candidate.ids)
        with self.assertRaisesRegex(UserError, "Reopen this Expense Batch"):
            target.with_user(self.expense_user_employee).write({
                "purpose": "Archived records are read-only",
            })
        target.active = True
        target.with_user(self.expense_user_employee).add_expenses(candidate.ids)
        self.assertEqual(candidate.expense_batch_id, target)

        draft_target = self._batch(
            self._expense("Toronto readonly grouping target"),
            name="Toronto readonly grouping",
        )
        reviewer = mail_new_test_user(
            self.env,
            name="Expense batch grouping reviewer",
            login="expense.batch.grouping.reviewer@example.invalid",
            groups="base.group_user,account.group_account_readonly",
            company_id=self.env.company.id,
            company_ids=[Command.set(self.env.companies.ids)],
        )
        with self.assertRaisesRegex(AccessError, "Read-only accountants"):
            draft_target.with_user(reviewer).add_expenses(candidate.ids)

    def test_outside_batch_dates_warn_without_blocking_processing(self):
        expense = self._expense("Toronto August receipt")
        expense.date = fields.Date.from_string("2026-08-02")
        batch = self.env["usl.expense.batch"].with_user(
            self.expense_user_employee,
        ).create({
            "name": "Toronto July context",
            "purpose": "Retain the intended period without rejecting evidence",
            "context_date_from": fields.Date.from_string("2026-07-01"),
            "context_date_to": fields.Date.from_string("2026-07-31"),
            "employee_id": self.expense_employee.id,
            "company_id": self.env.company.id,
        })
        wizard = self.env["usl.expense.batch.create.wizard"].create({
            "mode": "existing",
            "batch_id": batch.id,
            "expense_ids": [Command.set(expense.ids)],
        })

        self.assertIn("outside", wizard.outside_date_warning)
        self.assertEqual(wizard._create_batch(), batch)
        self.assertIn("outside", expense.batch_warning_reason)

        batch.with_user(self.expense_user_employee).action_submit()
        batch.with_user(self.expense_user_manager).action_approve()
        post_action = batch.with_user(self.env.user).action_post()
        if post_action:
            self.env[post_action["res_model"]].with_context(
                post_action["context"],
            ).browse(post_action["res_id"]).action_post_entry()
        self.assertEqual(expense.state, "posted")
        self.assertEqual(batch.expense_progress, "posted")
        self.assertTrue(batch.active)

    def test_submit_approve_and_return_one_expense(self):
        first = self._expense("Toronto flight")
        second = self._expense("Toronto meals", amount=43)
        batch = self._batch(first + second)

        batch.with_user(self.expense_user_employee).action_submit()
        self.assertEqual(first.state, "submitted")
        self.assertEqual(second.state, "submitted")
        self.assertEqual(batch.expense_progress, "submitted")
        self.assertEqual(batch.submitted_by_id, self.expense_user_employee)

        first.with_user(self.expense_user_manager).action_return_from_batch()
        self.assertFalse(first.expense_batch_id)
        self.assertEqual(first.state, "draft")
        self.assertEqual(second.expense_batch_id, batch)
        self.assertEqual(batch.expense_progress, "submitted")

        batch.with_user(self.expense_user_manager).action_approve()
        self.assertEqual(second.state, "approved")
        self.assertEqual(batch.expense_progress, "approved")
        self.assertEqual(batch.approved_by_id, self.expense_user_manager)

    def test_submission_blocks_incomplete_lines_without_partial_transition(self):
        complete = self._expense("Toronto conference")
        incomplete = self._expense("Toronto subway", with_receipt=False)
        batch = self._batch(complete + incomplete)

        with self.assertRaisesRegex(UserError, "Complete the following expenses"):
            batch.with_user(self.expense_user_employee).action_submit()
        self.assertEqual(complete.state, "draft")
        self.assertEqual(incomplete.state, "draft")
        self.assertEqual(batch.incomplete_expense_ids, incomplete)

    def test_employee_and_company_are_hard_compatibility_boundaries(self):
        first = self._expense("Toronto outbound")
        other_user = mail_new_test_user(
            self.env,
            name="Other expense employee",
            login="other.expense.employee@example.invalid",
            groups="base.group_user",
            company_id=self.env.company.id,
            company_ids=[Command.set(self.env.companies.ids)],
        )
        other_employee = self.env["hr.employee"].sudo().create({
            "name": other_user.name,
            "user_id": other_user.id,
            "expense_manager_id": self.expense_user_manager.id,
            "company_id": self.env.company.id,
        })
        second = self._expense(
            "Toronto return",
            employee=other_employee,
        )

        structural_expense = self._expense("Toronto fixed employee")
        batch = self._batch(structural_expense)
        with self.assertRaisesRegex(UserError, "cannot change after expenses"):
            batch.with_user(self.expense_user_employee).write({
                "employee_id": other_employee.id,
            })

        with self.assertRaisesRegex(
            ValidationError,
            "different employees",
        ), self.cr.savepoint():
            self.env["usl.expense.batch"].create(
                {
                    "name": "Invalid mixed claim",
                    "purpose": "Must remain separate",
                    "employee_id": self.expense_employee.id,
                    "company_id": self.env.company.id,
                    "expense_ids": [Command.set((first + second).ids)],
                },
            )

    def test_accounting_values_keep_batch_reference_and_expense_lines(self):
        first = self._expense("Toronto lodging", amount=900)
        second = self._expense("Toronto ground transport", amount=120)
        batch = self._batch(first + second)

        move_values = (first + second)._prepare_receipts_vals()
        self.assertEqual(len(move_values), 1)
        self.assertEqual(move_values[0]["expense_batch_id"], batch.id)
        self.assertEqual(move_values[0]["ref"], batch.name)
        self.assertEqual(len(move_values[0]["line_ids"]), 2)
        self.assertEqual(
            set((first + second).ids),
            set((first + second)._expense_ids_from_move_vals(move_values[0])),
        )

    def test_company_paid_values_keep_batch_reference_and_expense_line(self):
        expense = self._expense(
            "Toronto company card",
            amount=215,
            payment_mode="company_account",
        )
        batch = self._batch(expense)

        move_values, payment_values = expense._prepare_payments_vals()
        self.assertEqual(move_values["expense_batch_id"], batch.id)
        self.assertEqual(move_values["ref"], batch.name)
        self.assertEqual(payment_values["memo"], batch.name)
        self.assertTrue(
            any(
                line_values.get("expense_id") == expense.id
                for command, _record_id, line_values in move_values["line_ids"]
                if command == Command.CREATE
            ),
        )

    def test_shared_context_precedence_revision_idempotence_and_removal(self):
        inherited = self._expense("Toronto inherited", amount=121)
        exception = self._expense("Toronto exception", amount=122)
        original_account = inherited.account_id
        original_distribution = inherited.analytic_distribution
        exception.write({
            "account_id": original_account.id,
            "analytic_distribution": {str(self.analytic_account_2.id): 100},
        })
        batch_account = self.expense_account
        batch = self.env["usl.expense.batch"].with_user(
            self.expense_user_manager,
        ).create({
            "name": "SBFH — Canada 2026",
            "purpose": "SBFH travel workshops",
            "context_type": "travel",
            "context_date_from": fields.Date.from_string("2026-07-01"),
            "context_date_to": fields.Date.from_string("2026-07-31"),
            "employee_id": self.expense_employee.id,
            "company_id": self.env.company.id,
            "account_override_id": batch_account.id,
            "analytic_distribution": {str(self.analytic_account_1.id): 100},
            "expense_ids": [Command.set((inherited + exception).ids)],
        })

        self.assertEqual(inherited.account_id, batch_account)
        self.assertEqual(inherited.account_context_source, "batch")
        self.assertEqual(inherited.analytic_context_source, "batch")
        self.assertEqual(exception.account_id, original_account)
        self.assertEqual(exception.account_context_source, "explicit")
        self.assertEqual(
            exception.analytic_distribution,
            {str(self.analytic_account_2.id): 100},
        )
        self.assertEqual(exception.batch_context_status, "exception")
        self.assertEqual(exception.batch_attention_level, "warning")
        self.assertIn("analytics", exception.batch_attention_message)
        self.assertEqual(batch.exception_count, 1)

        inherited.product_id = self.product_a
        self.assertEqual(inherited.account_id, batch_account)
        self.assertEqual(
            inherited.analytic_distribution,
            {str(self.analytic_account_1.id): 100},
        )

        previous_revision = batch.context_revision
        batch.with_user(self.expense_user_employee).write({
            "analytic_distribution": {str(self.analytic_account_2.id): 100},
        })
        self.assertEqual(batch.context_revision, previous_revision + 1)
        self.assertEqual(inherited.batch_context_status, "stale")
        with self.assertRaisesRegex(UserError, "context changed"):
            batch.apply_context(expected_revision=previous_revision)
        applied = batch.apply_context(expected_revision=batch.context_revision)
        self.assertEqual(applied["applied"], 1)
        self.assertEqual(
            inherited.analytic_distribution,
            {str(self.analytic_account_2.id): 100},
        )
        self.assertEqual(
            batch.apply_context(expected_revision=batch.context_revision)["applied"],
            0,
        )

        inherited.expense_batch_id = False
        self.assertEqual(inherited.account_id, original_account)
        self.assertEqual(inherited.analytic_distribution, original_distribution)
        self.assertEqual(exception.expense_batch_id, batch)
        self.assertEqual(exception.account_context_source, "explicit")

    def test_matching_explicit_context_is_not_reported_as_an_exception(self):
        expense = self._expense("Toronto matching context")
        expense.with_context(usl_batch_context_internal=True).write({
            "account_context_source": "explicit",
            "analytic_context_source": "explicit",
        })
        batch = self.env["usl.expense.batch"].with_user(
            self.expense_user_manager,
        ).create({
            "name": "Matching context",
            "purpose": "Prove semantic comparison",
            "employee_id": self.expense_employee.id,
            "company_id": self.env.company.id,
            "account_override_id": expense.account_id.id,
            "analytic_distribution": expense.analytic_distribution,
            "expense_ids": [Command.set(expense.ids)],
        })

        self.assertEqual(expense.batch_context_status, "inherited")
        self.assertEqual(batch.exception_count, 0)
        self.assertEqual(batch.preview_context_application()["exceptions"], 0)
        self.assertFalse(expense.batch_attention_message)

    def test_analytic_distribution_comparison_is_order_independent(self):
        expense_model = self.env["hr.expense"]
        left = {f"{self.analytic_account_1.id},{self.analytic_account_2.id}": 100}
        right = {f"{self.analytic_account_2.id},{self.analytic_account_1.id}": 100.0}
        self.assertTrue(expense_model._analytic_distributions_equal(left, right))

    def test_batch_form_onchange_does_not_compute_duplicates_on_new_ids(self):
        expense = self._expense("Toronto onchange")
        batch = self._batch(expense)

        with Form(
            batch,
            view=self.env.ref("usl_expense_batch.view_expense_batch_form"),
        ) as batch_form:
            batch_form.purpose = "Updated safely through the Batch form"

        self.assertEqual(batch.purpose, "Updated safely through the Batch form")

    def test_candidate_service_and_create_or_select_flow(self):
        existing = self.env["usl.expense.batch"].with_user(
            self.expense_user_employee,
        ).create({
            "name": "SBFH — Canada 2026",
            "purpose": "Canada travel",
            "context_type": "travel",
            "context_date_from": fields.Date.from_string("2026-07-01"),
            "context_date_to": fields.Date.from_string("2026-07-31"),
            "employee_id": self.expense_employee.id,
            "company_id": self.env.company.id,
            "analytic_distribution": {str(self.analytic_account_1.id): 100},
        })
        self.assertEqual(existing.expense_progress, "empty")
        self.assertEqual(existing.expense_count, 0)
        with self.assertRaisesRegex(UserError, "Add at least one expense"):
            existing.action_submit()
        expense = self._expense("Canada candidate", amount=73)
        candidates = self.env["hr.expense"].get_expense_batch_candidates(
            expense.ids,
        )
        self.assertEqual(candidates[0]["id"], existing.id)
        self.assertTrue(candidates[0]["date_overlap"])
        self.assertEqual(candidates[0]["analytic_overlap"], 1)

        wizard = self.env["usl.expense.batch.create.wizard"].create({
            "expense_ids": [Command.set(expense.ids)],
        })
        self.assertEqual(wizard.mode, "existing")
        self.assertEqual(wizard.batch_id, existing)
        self.assertEqual(wizard._create_batch(), existing)
        self.assertEqual(expense.expense_batch_id, existing)

    def test_batch_presentation_summarizes_progress_without_changing_lifecycle(self):
        draft = self._expense("Draft receipt")
        approved = self._expense("Approved receipt")
        incomplete = self._expense("Missing receipt", with_receipt=False)
        approved.sudo().approval_state = "approved"
        batch = self._batch(draft + approved + incomplete, "Mixed progress")

        self.assertEqual(batch.batch_state, "open")
        self.assertEqual(batch.expense_progress, "draft")
        self.assertEqual(batch.expense_progress_summary, "2 draft · 1 approved")
        self.assertEqual(
            batch.expense_progress_breakdown,
            '{"draft":2,"approved":1}',
        )
        self.assertEqual(batch.attention_count, 3)
        self.assertEqual(batch.readiness_summary, "Needs attention · 3 issues")
        self.assertEqual(
            batch.period_summary,
            format_date(self.env, batch.date_from),
        )

        dashboard = self.env["usl.expense.batch"].get_batch_dashboard_counts()
        self.assertGreaterEqual(dashboard["all"], 1)
        self.assertGreaterEqual(dashboard["open_batches"], 1)
        self.assertGreaterEqual(dashboard["needs_information"], 1)
        self.assertIn(
            batch,
            self.env["usl.expense.batch"].search([
                ("has_incomplete_expenses", "=", True),
            ]),
        )
        self.assertEqual(
            set(dashboard),
            {"all", "open_batches", "needs_information", "my_batches", "exceptions"},
        )

        batch.active = False
        self.assertEqual(batch.batch_state, "archived")

    def test_add_expenses_action_uses_eligible_same_employee_records(self):
        batch = self._batch(self._expense("Existing expense"), "Open batch")
        candidate = self._expense("Later expense")

        action = batch.action_open_add_expenses_wizard()
        wizard = self.env[action["res_model"]].browse(action["res_id"])
        self.assertEqual(wizard.batch_id, batch)
        wizard.expense_ids = candidate
        self.assertEqual(
            wizard.action_add(),
            {"type": "ir.actions.act_window_close"},
        )
        self.assertEqual(candidate.expense_batch_id, batch)

    def test_mixed_payer_posting_keeps_one_batch_and_remaining_action(self):
        employee_paid = self._expense("Canada hotel", amount=215)
        company_paid = self._expense(
            "Canada company card",
            amount=76,
            payment_mode="company_account",
        )
        batch = self._batch(employee_paid + company_paid, "SBFH — Canada 2026")
        batch.with_user(self.expense_user_employee).action_submit()
        batch.with_user(self.expense_user_manager).action_approve()

        post_action = batch.with_user(self.env.user).action_post()
        self.assertEqual(post_action["res_model"], "hr.expense.post.wizard")
        self.assertTrue(company_paid.account_move_id)
        self.assertEqual(company_paid.account_move_id.expense_batch_id, batch)
        self.assertEqual(company_paid.account_move_id.ref, batch.name)
        self.assertEqual(employee_paid.state, "approved")
        self.assertEqual(batch.expense_progress, "approved")
        self.assertEqual(batch.employee_paid_open_count, 1)
        self.assertEqual(batch.company_paid_open_count, 0)

        post_wizard = self.env[post_action["res_model"]].with_context(
            post_action["context"],
        ).browse(post_action["res_id"])
        post_wizard.action_post_entry()
        self.assertEqual(employee_paid.state, "posted")
        self.assertEqual(employee_paid.account_move_id.expense_batch_id, batch)
        self.assertEqual(employee_paid.account_move_id.ref, batch.name)
        self.assertEqual(batch.expense_progress, "posted")
        self.assertEqual(batch.accounting_reconciliation_state, "matched")
        self.assertEqual(batch.accounting_difference, 0.0)

    def test_employee_cannot_set_general_ledger_override(self):
        batch = self._batch(self._expense("Canada access", amount=57))
        with self.assertRaisesRegex(AccessError, "Only Expense or Accounting Managers"):
            batch.with_user(self.expense_user_employee).write({
                "account_override_id": self.expense_account.id,
            })
        batch.with_user(self.expense_user_employee).write({
            "analytic_distribution": {str(self.analytic_account_2.id): 100},
        })
        self.assertEqual(
            batch.analytic_distribution,
            {str(self.analytic_account_2.id): 100},
        )

        accounting_manager = mail_new_test_user(
            self.env,
            name="Expense batch accounting manager",
            login="expense.batch.accounting.manager@example.invalid",
            groups="base.group_user,account.group_account_manager",
            company_id=self.env.company.id,
            company_ids=[Command.set(self.env.companies.ids)],
        )
        self.assertTrue(
            accounting_manager.has_group("hr_expense.group_hr_expense_manager"),
        )
        managed_batch = batch.with_user(accounting_manager)
        self.assertEqual(managed_batch.name, batch.name)
        managed_batch.account_override_id = self.expense_account
        self.assertEqual(batch.account_override_id, self.expense_account)

    def test_explicit_and_inferred_provenance_is_not_silently_reclassified(self):
        explicit = self.env["hr.expense"].create({
            "name": "Deliberate analytic exception",
            "date": fields.Date.from_string("2026-07-10"),
            "employee_id": self.expense_employee.id,
            "product_id": self.product_c.id,
            "company_id": self.env.company.id,
            "payment_mode": "own_account",
            "total_amount_currency": 49,
            "analytic_distribution": {str(self.analytic_account_2.id): 100},
        })
        self.assertEqual(explicit.analytic_context_source, "explicit")

        suggested = self._expense("Inferred analytic suggestion", amount=51)
        suggested.write({
            "analytic_distribution": {str(self.analytic_account_2.id): 100},
            "analytic_context_source": "inferred",
        })
        self.assertEqual(suggested.analytic_context_source, "inferred")

    def test_readonly_accountant_can_review_but_cannot_mutate(self):
        expense = self._expense("Toronto review")
        batch = self._batch(expense)
        reviewer = mail_new_test_user(
            self.env,
            name="Expense batch reviewer",
            login="expense.batch.reviewer@example.invalid",
            groups="base.group_user,account.group_account_readonly",
            company_id=self.env.company.id,
            company_ids=[Command.set(self.env.companies.ids)],
        )
        self.assertEqual(
            self.env["usl.expense.batch"].with_user(reviewer).browse(batch.id).name,
            batch.name,
        )
        self.assertEqual(expense.with_user(reviewer).name, expense.name)
        with self.assertRaises(AccessError):
            expense.with_user(reviewer).write({"name": "Forbidden expense edit"})
        with self.assertRaisesRegex(AccessError, "Read-only accountants"):
            batch.with_user(reviewer).write({"purpose": "Forbidden edit"})
        with self.assertRaisesRegex(AccessError, "Read-only accountants"):
            batch.with_user(reviewer).action_submit()

        form = self.env["usl.expense.batch"].with_user(reviewer).get_view(
            self.env.ref("usl_expense_batch.view_expense_batch_form").id,
            "form",
        )
        self.assertIn('edit="false"', form["arch"])
        self.assertNotIn('name="action_submit"', form["arch"])
        self.assertNotIn('name="action_return_from_batch"', form["arch"])

        expense_form = self.env["hr.expense"].with_user(reviewer).get_view(
            self.env.ref("hr_expense.hr_expense_view_form").id,
            "form",
        )
        self.assertIn('edit="false"', expense_form["arch"])
        self.assertNotIn('name="action_submit"', expense_form["arch"])
        self.assertNotIn('name="action_split_wizard"', expense_form["arch"])
        self.assertNotIn('name="attach_document"', expense_form["arch"])

    def test_views_keep_readiness_out_of_list_and_expose_drill_down(self):
        expense_list = self.env.ref(
            "hr_expense.hr_expense_view_expenses_analysis_tree",
        )._get_combined_arch()
        self.assertFalse(expense_list.xpath("//field[@name='batch_readiness']"))
        self.assertTrue(
            expense_list.xpath("//field[@name='batch_attachment_status']"),
        )
        self.assertTrue(expense_list.xpath("//field[@name='expense_batch_id']"))

        expense_search = self.env.ref(
            "hr_expense.hr_expense_view_search",
        )._get_combined_arch()
        for filter_name in (
            "needs_batching",
            "batch_ready",
            "batch_incomplete",
            "already_batched",
        ):
            self.assertTrue(
                expense_search.xpath(f"//filter[@name='{filter_name}']"),
            )
        needs_batching = expense_search.xpath(
            "//filter[@name='needs_batching']",
        )[0]
        self.assertEqual(needs_batching.get("string"), "Needs batching")
        self.assertEqual(
            needs_batching.get("domain"),
            "[('expense_batch_id', '=', False), "
            "('state', 'in', ['draft', 'approved', 'posted'])]",
        )
        my_expenses_action = self.env.ref(
            "hr_expense.hr_expense_actions_my_all",
        )
        self.assertIn(
            "'search_default_needs_batching': 1",
            my_expenses_action.context,
        )
        self.assertNotIn(
            "search_default_not_in_batch",
            my_expenses_action.context,
        )

        expense_root = self.env.ref("hr_expense.menu_hr_expense_root")
        self.assertEqual(expense_root.action, my_expenses_action)
        self.assertFalse(
            self.env.ref("hr_expense.menu_hr_expense_my_expenses").active,
        )
        self.assertFalse(
            self.env.ref("hr_expense.menu_hr_expense_my_expenses_all").active,
        )
        self.assertEqual(
            self.env.ref(
                "hr_expense.menu_hr_expense_expenses_to_process",
            ).parent_id,
            expense_root,
        )
        self.assertEqual(
            self.env.ref("usl_expense_batch.menu_expense_batches").parent_id,
            expense_root,
        )
        self.assertEqual(
            self.env.ref("hr_expense.menu_hr_expense_reports").parent_id,
            expense_root,
        )
        self.assertEqual(
            self.env.ref("hr_expense.menu_hr_expense_configuration").parent_id,
            expense_root,
        )

        move_form = self.env.ref("account.view_move_form")._get_combined_arch()
        self.assertTrue(
            move_form.xpath("//button[@name='action_open_expense_batch']"),
        )

        batch_form = self.env.ref(
            "usl_expense_batch.view_expense_batch_form",
        )._get_combined_arch()
        expense_lines = batch_form.xpath(
            "//field[@name='expense_ids']/list",
        )[0]
        self.assertFalse(
            expense_lines.xpath("./field[@name='batch_readiness']"),
        )
        self.assertTrue(
            expense_lines.xpath("./field[@name='batch_attachment_status']"),
        )
        self.assertEqual(expense_lines.get("delete"), "false")
        self.assertFalse(
            expense_lines.xpath("./field[@name='batch_context_status']"),
        )
        attention = expense_lines.xpath(
            "./field[@name='batch_attention_level']",
        )[0]
        self.assertEqual(attention.get("widget"), "expense_batch_attention")
        self.assertEqual(attention.get("string"), "")
        self.assertTrue(expense_lines.xpath("./field[@name='state']"))
        missing_information = expense_lines.xpath(
            "./field[@name='batch_incomplete_reason']",
        )
        self.assertEqual(
            missing_information[0].get("string"),
            "Missing information",
        )
        self.assertFalse(batch_form.xpath("//field[@name='readiness_state']"))
        self.assertFalse(
            batch_form.xpath("//field[@name='expense_progress_summary']"),
        )
        self.assertFalse(batch_form.xpath("//field[@name='product_summary']"))
        self.assertTrue(batch_form.xpath("//field[@name='readiness_summary']"))
        self.assertFalse(
            batch_form.xpath("//div[contains(@class, 'alert-warning')]"),
        )
        self.assertTrue(
            batch_form.xpath(
                "//div[contains(@class, 'o_usl_batch_attention_summary')]"
                "//field[@name='attention_count']",
            ),
        )
        self.assertTrue(
            batch_form.xpath(
                "//button[@name='action_open_add_expenses_wizard']",
            ),
        )
        self.assertFalse(batch_form.xpath("/form/header/field[@name='state']"))
        self.assertTrue(
            batch_form.xpath("//widget[@name='web_ribbon'][@text='Archived']"),
        )
        self.assertFalse(batch_form.xpath("//page[@name='review']"))
        self.assertTrue(batch_form.xpath("//page[@name='accounting_history']"))
        analytic_fields = batch_form.xpath(
            "//group[@string='Shared context']//field[@name='analytic_distribution']",
        )
        self.assertEqual(analytic_fields[0].get("widget"), "analytic_distribution")
        self.assertNotEqual(analytic_fields[0].get("invisible"), "1")
        self.assertEqual(analytic_fields[0].get("readonly"), "not active")
        context_period = batch_form.xpath(
            "//group[@string='Shared context']//div[@name='context_period']",
        )
        self.assertEqual(
            context_period[0].xpath("./field/@name"),
            ["context_date_from", "context_date_to"],
        )
        remove_actions = expense_lines.xpath(
            "./button[@name='action_return_from_batch']",
        )
        self.assertEqual(
            {button.get("string") for button in remove_actions},
            {"Remove from Batch", "Return for correction"},
        )
        apply_context = batch_form.xpath(
            "//button[@name='action_open_context_wizard']",
        )[0]
        self.assertIn("shared expense account", apply_context.get("title"))
        self.assertIn("preview", apply_context.get("title").lower())
        self.assertIn("draft_expense_count == 0", apply_context.get("invisible"))
        add_expenses = batch_form.xpath(
            "//button[@name='action_open_add_expenses_wizard']",
        )[0]
        self.assertIn("Existing accounting entries", add_expenses.get("title"))
        self.assertIn("expense states remain unchanged", add_expenses.get("title"))
        submit = batch_form.xpath("//button[@name='action_submit']")[0]
        self.assertIn("eligible draft expense", submit.get("title"))
        self.assertIn("remain unchanged", submit.get("title").lower())
        self.assertIn("draft_expense_count == 0", submit.get("invisible"))

        batch_search = self.env.ref(
            "usl_expense_batch.view_expense_batch_search",
        )._get_combined_arch()
        for filter_name in (
            "open_batches",
            "needs_information",
            "my_batches",
            "exceptions",
        ):
            self.assertTrue(
                batch_search.xpath(f"//filter[@name='{filter_name}']"),
            )
        archived = batch_search.xpath("//filter[@name='inactive']")[0]
        self.assertEqual(archived.get("domain"), "[('active', '=', False)]")

        batch_list = self.env.ref(
            "usl_expense_batch.view_expense_batch_list",
        )._get_combined_arch()
        self.assertFalse(batch_list.xpath("/list[@decoration-info]"))
        self.assertEqual(batch_list.get("js_class"), "usl_expense_batch_list")
        self.assertTrue(batch_list.xpath("//field[@name='period_summary']"))
        self.assertFalse(batch_list.xpath("//field[@name='date_from']"))
        self.assertFalse(batch_list.xpath("//field[@name='date_to']"))
        self.assertTrue(
            batch_list.xpath("//field[@name='expense_progress_summary']"),
        )
        progress_summary = batch_list.xpath(
            "//field[@name='expense_progress_summary']",
        )[0]
        self.assertIn("o_usl_progress_summary", progress_summary.get("class"))
        self.assertEqual(
            progress_summary.get("widget"),
            "expense_batch_progress",
        )
        self.assertTrue(
            batch_list.xpath(
                "//field[@name='expense_progress_breakdown']"
                "[@column_invisible='True']",
            ),
        )
        self.assertTrue(batch_list.xpath("//field[@name='batch_state']"))

        context_wizard = self.env.ref(
            "usl_expense_batch.view_expense_batch_context_apply_wizard_form",
        )._get_combined_arch()
        apply_context = context_wizard.xpath(
            "//button[@name='action_apply']",
        )[0]
        self.assertIn("Explicit line exceptions", apply_context.get("title"))

        wizard_form = self.env.ref(
            "usl_expense_batch.view_expense_batch_create_wizard_form",
        )._get_combined_arch()
        self.assertTrue(
            wizard_form.xpath("//field[@name='outside_date_warning']"),
        )
        wizard_lines = wizard_form.xpath(
            "//field[@name='expense_ids']/list",
        )[0]
        self.assertFalse(
            wizard_lines.xpath("./field[@name='batch_readiness']"),
        )
        self.assertTrue(
            wizard_lines.xpath("./field[@name='batch_attachment_status']"),
        )
        self.assertTrue(wizard_lines.xpath("./field[@name='state']"))
        wizard_submit = wizard_form.xpath(
            "//button[@name='action_create_and_submit']",
        )[0]
        self.assertIn("only its draft expenses", wizard_submit.get("title"))
        self.assertIn(
            "draft_incomplete_count",
            wizard_submit.get("invisible"),
        )

        batch = self._batch(self._expense("Toronto action contract"))
        self.assertEqual(
            batch.expense_ids.action_open_expense_batch()["views"],
            [(False, "form")],
        )
        self.assertEqual(
            self.env["account.move"].new({
                "expense_batch_id": batch.id,
            }).action_open_expense_batch()["views"],
            [(False, "form")],
        )
