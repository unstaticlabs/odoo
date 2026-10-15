from odoo import SUPERUSER_ID, Command
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.addons.hr_expense.tests.common import TestExpenseCommon

from ..controllers.json2 import UslAgentJson2Controller


@tagged("post_install", "-at_install", "usl_access_control")
class TestAgentReceiptDecisions(TransactionCase):
    """An owner may let their Agent record a receipt decision, under conditions."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.expense_manager = cls.env.ref("hr_expense.group_hr_expense_manager")
        cls.owner = cls._create_user("receipt.owner", cls.expense_manager)
        cls.employee = cls.env["hr.employee"].with_user(SUPERUSER_ID).create(
            {
                "name": "Receipt employee",
                "company_id": cls.company.id,
                "user_id": cls.owner.id,
            },
        )
        cls.category = cls.env["product.product"].with_user(SUPERUSER_ID).create(
            {
                "name": "Taxi",
                "type": "service",
                "can_be_expensed": True,
                "standard_price": 0.0,
                "list_price": 0.0,
                "rebuild_receipt_required": True,
            },
        )

    @classmethod
    def _create_user(cls, login, *groups):
        return cls.env["res.users"].with_user(SUPERUSER_ID).with_context(
            no_reset_password=True,
            usl_governed_identity_provisioning=True,
        ).create(
            {
                "name": login,
                "login": login,
                "email": f"{login}@example.test",
                "company_id": cls.company.id,
                "company_ids": [Command.set(cls.company.ids)],
                "group_ids": [Command.set([cls.env.ref("base.group_user").id, *[group.id for group in groups]])],
                "usl_identity_classification": "active",
            },
        )

    def _create_agent(self, delegated_groups=None, access_mode="read_write"):
        delegated_groups = self.expense_manager if delegated_groups is None else delegated_groups
        return self.env["usl.agent"].with_user(self.owner).create(
            {
                "name": "Expense assistant",
                "purpose": "Prepare expenses for the accounting review.",
                "owner_id": self.owner.id,
                "company_id": self.company.id,
                "company_ids": [Command.set(self.company.ids)],
                "delegated_group_ids": [Command.set(delegated_groups.ids)],
                "access_mode": access_mode,
            },
        )

    def _create_expense(self, reason="The taxi issued no ticket."):
        expense = self.env["hr.expense"].with_user(SUPERUSER_ID).create(
            {
                "name": "Taxi without a printed ticket",
                "employee_id": self.employee.id,
                "product_id": self.category.id,
                "company_id": self.company.id,
                "payment_mode": "own_account",
                "total_amount_currency": 42.0,
            },
        )
        expense.rebuild_receipt_waiver_reason = reason
        self.assertEqual(expense.rebuild_receipt_state, "missing")
        return expense

    def test_agent_creates_draft_expense_despite_empty_activity_cleanup(self):
        agent = self._create_agent()
        context = UslAgentJson2Controller._agent_call_context(
            context={}, agent=agent, model_name="hr.expense", method_name="create", access="write",
        )
        expense = self.env["hr.expense"].with_user(agent.user_id).with_context(context).create({
            "name": "[QA] Draft preparation",
            "employee_id": self.employee.id,
            "product_id": self.category.id,
            "company_id": self.company.id,
            "payment_mode": "own_account",
            "total_amount_currency": 42.0,
        })
        self.assertEqual(expense.state, "draft")
        self.assertEqual(expense.create_uid, agent.user_id)
        self.assertFalse(expense.account_move_id)

    def _as_agent_operation(self, expense, agent, method="write"):
        context = UslAgentJson2Controller._agent_call_context(
            context={}, agent=agent, model_name="hr.expense", method_name=method, access="write",
        )
        return expense.with_user(agent.user_id).with_context(context)

    def test_agent_generic_approved_financial_write_is_denied_atomically(self):
        agent = self._create_agent()
        approved = self._create_expense()
        approved.approval_state = "approved"
        draft = self._create_expense()
        before = (draft.total_amount_currency, approved.total_amount_currency)
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            self._as_agent_operation(draft | approved, agent).write({"total_amount_currency": 60.0})
        self.assertEqual((draft.total_amount_currency, approved.total_amount_currency), before)
        self.assertEqual(approved.state, "approved")

    def test_agent_cannot_launder_approved_expense_into_draft(self):
        agent = self._create_agent()
        expense = self._create_expense()
        expense.approval_state = "approved"
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            self._as_agent_operation(expense, agent).write({"approval_state": False})
        expense.invalidate_recordset()
        self.assertEqual(expense.state, "approved")
        self.assertEqual(expense.total_amount_currency, 42.0)

    def test_agent_web_save_denies_approved_financial_inputs_with_any_readback(self):
        agent = self._create_agent()
        expense = self._create_expense()
        expense.approval_state = "approved"
        for specification in ({}, {"total_amount_currency": {}, "state": {}}):
            with self.subTest(specification=specification):
                with self.assertRaises(AccessError), self.env.cr.savepoint():
                    self._as_agent_operation(expense, agent, "web_save").web_save(
                        {"total_amount_currency": 60.0}, specification,
                    )
        self.assertEqual(expense.total_amount_currency, 42.0)

    def test_agent_sudo_and_serializable_context_do_not_authorize_financial_edits(self):
        agent = self._create_agent()
        expense = self._create_expense()
        expense.approval_state = "approved"
        for candidate in (expense.with_user(agent.user_id).sudo(),
                          self._as_agent_operation(expense, agent).sudo(),
                          expense.with_user(agent.user_id).with_context(
                              usl_batch_context_internal=True,
                              usl_agent_operation_scope={"root_model": "hr.expense", "root_method": "action_post", "access": "write"})):
            with self.assertRaises(AccessError), self.env.cr.savepoint():
                candidate.write({"total_amount_currency": 60.0})
        self.assertEqual(expense.total_amount_currency, 42.0)

    def test_agent_generic_lifecycle_writes_are_denied_even_on_draft(self):
        agent = self._create_agent()
        expense = self._create_expense()
        for values in ({"approval_state": "approved"}, {"state": "approved"},
                       {"account_move_id": False}):
            with self.subTest(values=values):
                with self.assertRaises(AccessError), self.env.cr.savepoint():
                    self._as_agent_operation(expense, agent).write(values)
        self.assertEqual(expense.state, "draft")
        self.assertFalse(expense.account_move_id)

    def test_human_manager_keeps_native_approved_edit_and_agent_keeps_notes(self):
        expense = self._create_expense()
        expense.approval_state = "approved"
        expense.with_user(self.owner).write({"total_amount_currency": 60.0})
        self.assertEqual(expense.total_amount_currency, 60.0)
        agent = self._create_agent()
        self._as_agent_operation(expense, agent).write({"name": "[QA] Approved note", "description": "[QA] Evidence clarified"})
        self.assertEqual(expense.name, "[QA] Approved note")
        self.assertEqual(expense.state, "approved")

    def test_agent_generic_draft_financial_write_keeps_native_authority(self):
        agent = self._create_agent()
        expense = self._create_expense()
        self._as_agent_operation(expense, agent).write({"total_amount_currency": 60.0})
        self.assertEqual(expense.total_amount_currency, 60.0)
        self.assertEqual(expense.state, "draft")
        readonly = self._create_agent(access_mode="read_only")
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            self._as_agent_operation(expense, readonly).write({"total_amount_currency": 90.0})
        self.assertEqual(expense.total_amount_currency, 60.0)

    def test_agent_cannot_forge_approved_batch_restoration_baseline(self):
        agent = self._create_agent()
        expense = self._create_expense()
        accounts = self.env["account.account"].create([
            {"name": "[QA] Original cost", "code": "QA6101", "account_type": "expense"},
            {"name": "[QA] Shared cost", "code": "QA6102", "account_type": "expense"},
            {"name": "[QA] Forged cost", "code": "QA6103", "account_type": "expense"},
        ])
        expense.account_id = accounts[0]
        batch = self.env["usl.expense.batch"].create({
            "name": "[QA] Baseline guard", "purpose": "[QA] Compare restoration", "company_id": self.company.id,
            "employee_id": self.employee.id, "account_override_id": accounts[1].id,
        })
        batch.add_expenses(expense.ids)
        batch.apply_context(force_expense_ids=expense.ids)
        self.assertEqual(expense.pre_batch_account_id, accounts[0])
        expense.approval_state = "approved"
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            self._as_agent_operation(expense, agent).write({"pre_batch_account_id": accounts[2].id})
        self.assertEqual(expense.pre_batch_account_id, accounts[0])
        self._as_agent_operation(expense, agent).write({"expense_batch_id": False})
        self.assertFalse(expense.expense_batch_id)
        self.assertEqual(expense.account_id, accounts[0])
        self.assertEqual(expense.state, "approved")

    def test_agent_financial_write_cannot_cross_company_even_with_scoped_sudo(self):
        agent = self._create_agent()
        company = self.env["res.company"].create({"name": "[QA] Foreign financial company"})
        employee = self.env["hr.employee"].create({"name": "[QA] Foreign financial employee", "company_id": company.id})
        expense = self.env["hr.expense"].with_company(company).create({
            "name": "[QA] Foreign financial draft", "company_id": company.id,
            "employee_id": employee.id, "product_id": self.category.id,
            "total_amount_currency": 120, "payment_mode": "own_account",
        })
        candidate = self._as_agent_operation(expense, agent).with_context(allowed_company_ids=self.company.ids)
        for target in (candidate, candidate.sudo()):
            with self.assertRaises(AccessError), self.env.cr.savepoint():
                target.write({"total_amount_currency": 60})
        self.assertEqual(expense.total_amount_currency, 120)
        owned = self._create_expense()
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            self._as_agent_operation(owned, agent).sudo().write({"company_id": company.id, "employee_id": employee.id})
        self.assertEqual(owned.company_id, self.company)

    def test_agent_cannot_forge_approved_native_net_and_tax_totals(self):
        agent = self._create_agent()
        expense = self._create_expense()
        expense.approval_state = "approved"
        fields = ["untaxed_amount_currency", "tax_amount_currency", "untaxed_amount", "tax_amount"]
        before = expense.read(fields)[0]
        for field in fields:
            with self.subTest(field=field):
                with self.assertRaises(AccessError), self.env.cr.savepoint():
                    self._as_agent_operation(expense, agent).write({field: 99.0})
        expense.invalidate_recordset()
        self.assertEqual(expense.read(fields)[0], before)
        self.assertEqual(expense.state, "approved")

    def test_agent_cleans_only_expense_approval_activities(self):
        agent = self._create_agent()
        expense = self._create_expense()
        approval = expense.activity_schedule("hr_expense.mail_act_expense_approval", user_id=self.owner.id)
        todo = expense.activity_schedule("mail.mail_activity_data_todo", user_id=self.owner.id)
        other = self._create_expense()
        unrelated = other.activity_schedule("hr_expense.mail_act_expense_approval", user_id=self.owner.id)
        context = UslAgentJson2Controller._agent_call_context(
            context={}, agent=agent, model_name="hr.expense", method_name="update_activities_and_mails", access="write",
        )
        expense.with_user(agent.user_id).with_context(context).update_activities_and_mails()
        self.assertFalse(approval.exists())
        self.assertTrue(todo.exists())
        self.assertTrue(unrelated.exists())
        with self.assertRaises(AccessError):
            todo.with_user(agent.user_id).unlink()
        with self.assertRaises(AccessError):
            expense.with_user(agent.user_id).activity_unlink(["mail.mail_activity_data_todo"])

    def test_agent_resets_unposted_expense_and_preserves_other_activities(self):
        agent = self._create_agent()
        expense = self._create_expense()
        expense.with_user(SUPERUSER_ID).write({"approval_state": "approved"})
        approval = expense.activity_schedule("hr_expense.mail_act_expense_approval", user_id=self.owner.id)
        todo = expense.activity_schedule("mail.mail_activity_data_todo", user_id=self.owner.id)
        context = UslAgentJson2Controller._agent_call_context(
            context={}, agent=agent, model_name="hr.expense", method_name="action_reset", access="write",
        )
        expense.with_user(agent.user_id).with_context(context).action_reset()
        self.assertEqual(expense.state, "draft")
        self.assertFalse(expense.account_move_id)
        self.assertFalse(approval.exists())
        self.assertTrue(todo.exists())

    def test_expense_cleanup_denials_preserve_expense_and_activity(self):
        agent = self._create_agent(access_mode="read_only")
        expense = self._create_expense()
        activity = expense.activity_schedule("hr_expense.mail_act_expense_approval", user_id=self.owner.id)
        before = (expense.state, expense.approval_state, activity.id)
        forged = {"usl_agent_collaboration_token": True, "usl_agent_operation_scope": {"root_model": "hr.expense", "access": "write"}}
        with self.assertRaises(AccessError):
            expense.with_user(agent.user_id).with_context(forged).update_activities_and_mails()
        with self.assertRaises(AccessError):
            activity.with_user(agent.user_id).with_context(forged).unlink()
        self.assertEqual((expense.state, expense.approval_state, activity.id), before)
        self.assertTrue(activity.exists())
        company = self.env["res.company"].create({"name": "[QA] Outside expense company"})
        employee = self.env["hr.employee"].with_user(SUPERUSER_ID).create({"name": "[QA] Other company employee", "company_id": company.id})
        foreign = self.env["hr.expense"].with_user(SUPERUSER_ID).with_company(company).create({
            "name": "[QA] Other company expense", "employee_id": employee.id,
            "product_id": self.category.id, "company_id": company.id,
            "payment_mode": "own_account", "total_amount_currency": 42.0,
        })
        foreign_activity = foreign.activity_schedule("hr_expense.mail_act_expense_approval", user_id=self.owner.id)
        writable = self._create_agent()
        with self.assertRaises(AccessError):
            foreign.with_user(writable.user_id).with_context(allowed_company_ids=self.company.ids).update_activities_and_mails()
        self.assertEqual(foreign.state, "draft")
        self.assertTrue(foreign_activity.exists())

    def test_an_agent_without_the_authority_is_refused_and_told_who_can_grant_it(self):
        agent = self._create_agent()
        expense = self._create_expense()

        with self.assertRaises(AccessError) as denied:
            expense.with_user(agent.user_id).action_rebuild_waive_receipt()

        message = str(denied.exception)
        self.assertIn("a person must record", message)
        self.assertIn(self.owner.name, message)
        self.assertFalse(expense.rebuild_receipt_waived_at)

    def test_the_authority_needs_the_expense_access_it_stands_on(self):
        agent = self._create_agent(
            delegated_groups=self.env.ref("base.group_user"),
            access_mode="read_only",
        )

        with self.assertRaisesRegex(ValidationError, "write access to Expenses"):
            agent.with_user(self.owner).write({"expense_receipt_waiver_authority": True})

        self.assertFalse(agent.expense_receipt_waiver_authority)

    def test_an_agent_cannot_grant_itself_the_authority(self):
        agent = self._create_agent()
        expense = self._create_expense()

        with self.assertRaises(AccessError):
            agent.with_user(agent.user_id).write(
                {"expense_receipt_waiver_authority": True},
            )

        self.assertFalse(agent.expense_receipt_waiver_authority)
        with self.assertRaises(AccessError):
            expense.with_user(agent.user_id).action_rebuild_waive_receipt()

    def test_a_granted_agent_previews_the_decision_and_records_nothing(self):
        agent = self._create_agent()
        agent.with_user(self.owner).write({"expense_receipt_waiver_authority": True})
        expense = self._create_expense()

        preview = expense.with_user(agent.user_id).action_rebuild_waive_receipt()

        self.assertTrue(preview["confirmation_required"])
        self.assertEqual(preview["ids"], expense.ids)
        self.assertEqual(
            preview["confirm_with"]["kwargs"],
            {"agent_confirmed": True},
        )
        self.assertIn("Nothing has been recorded", preview["warning"])
        self.assertIn(self.owner.name, preview["ask_a_person"])
        self.assertEqual(preview["expenses"][0]["reason"], "The taxi issued no ticket.")
        self.assertFalse(expense.rebuild_receipt_waived_at)
        self.assertEqual(expense.rebuild_receipt_state, "missing")

    def test_the_preview_refuses_whatever_the_confirmation_would_refuse(self):
        agent = self._create_agent()
        agent.with_user(self.owner).write({"expense_receipt_waiver_authority": True})
        expense = self._create_expense(reason="   ")

        with self.assertRaisesRegex(UserError, "Explain why no receipt"):
            expense.with_user(agent.user_id).action_rebuild_waive_receipt()

        self.assertFalse(expense.rebuild_receipt_waived_at)

    def test_a_confirmed_decision_names_the_agent_and_the_human_authority(self):
        agent = self._create_agent()
        agent.with_user(self.owner).write({"expense_receipt_waiver_authority": True})
        expense = self._create_expense()

        expense.with_user(agent.user_id).action_rebuild_waive_receipt(
            agent_confirmed=True,
        )

        self.assertEqual(expense.rebuild_receipt_state, "waived")
        self.assertEqual(expense.rebuild_receipt_waived_by_id, agent.user_id)
        self.assertTrue(expense.rebuild_receipt_waived_at)
        note = expense.message_ids.filtered(
            lambda message: "continues without a receipt" in (message.body or ""),
        )
        self.assertEqual(len(note), 1)
        self.assertIn("The taxi issued no ticket.", note.body)
        self.assertIn(agent.name, note.body)
        self.assertIn(self.owner.name, note.body)

    def test_a_person_records_the_decision_in_one_call_without_agent_attribution(self):
        expense = self._create_expense()

        self.assertTrue(
            expense.with_user(self.owner).action_rebuild_waive_receipt(),
        )

        self.assertEqual(expense.rebuild_receipt_waived_by_id, self.owner)
        note = expense.message_ids.filtered(
            lambda message: "continues without a receipt" in (message.body or ""),
        )
        self.assertNotIn("on the receipt-decision authority", note.body)

    def test_withdrawing_the_expense_access_withdraws_the_receipt_authority(self):
        agent = self._create_agent()
        agent.with_user(self.owner).write({"expense_receipt_waiver_authority": True})
        expense = self._create_expense()

        self.owner.with_user(SUPERUSER_ID).write(
            {"group_ids": [Command.unlink(self.expense_manager.id)]},
        )
        self.env["usl.agent"]._reconcile_all()

        self.assertFalse(agent.expense_receipt_waiver_authority)
        self.assertTrue(agent.authority_reduced_at)
        with self.assertRaises(AccessError):
            expense.with_user(agent.user_id).action_rebuild_waive_receipt(
                agent_confirmed=True,
            )
        self.assertFalse(expense.rebuild_receipt_waived_at)


@tagged("post_install", "-at_install", "usl_access_control")
class TestAgentExpenseWorkflows(TestExpenseCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.owner = cls.expense_user_manager
        cls.groups = cls.env.ref("hr_expense.group_hr_expense_manager") | cls.env.ref("account.group_account_manager")
        cls.owner.write({"group_ids": [Command.link(group.id) for group in cls.groups],
                         "usl_identity_classification": "active"})
        cls.product_c.rebuild_receipt_required = False
        cls.bank = cls.company_data["default_journal_bank"]
        cls.method = cls.bank.outbound_payment_method_line_ids.filtered(lambda line: line.code == "manual")[:1]
        cls.method.payment_account_id = cls.env["account.chart.template"].ref("account_journal_payment_credit_account_id")
        cls.company.company_expense_allowed_payment_method_line_ids = [Command.set(cls.method.ids)]

    def _agent(self):
        return self.env["usl.agent"].with_user(self.owner).create({
            "name": "[QA] Expense workflow Agent", "purpose": "[QA] Native workflow controls",
            "owner_id": self.owner.id, "company_id": self.company.id,
            "company_ids": [Command.set(self.company.ids)],
            "delegated_group_ids": [Command.set(self.groups.ids)], "access_mode": "read_write",
        })

    def _operation(self, records, agent, method, **context):
        scope = UslAgentJson2Controller._agent_call_context(
            context=context, agent=agent, model_name=records._name, method_name=method, access="write",
        )
        return records.with_user(agent.user_id).with_context(scope)

    def _expense(self, amount, *, payer="own_account"):
        return self.env["hr.expense"].create({
            "name": f"[QA] Native workflow {amount}", "employee_id": self.expense_employee.id,
            "company_id": self.company.id, "product_id": self.product_c.id,
            "date": "2026-10-14", "tax_ids": [Command.clear()],
            "total_amount_currency": amount, "payment_mode": payer,
            "payment_method_line_id": self.method.id,
        })

    def test_agent_split_under_authenticated_operation_root_stays_draft(self):
        agent = self._agent()
        expense = self._expense(160)
        result = self._operation(expense, agent, "split_draft_expense").split_draft_expense([
            {"total_amount_currency": 60}, {"total_amount_currency": 100},
        ])
        family = self.env["hr.expense"].browse([part["id"] for part in result["expenses"]])
        self.assertEqual(len(family), 2)
        self.assertEqual(family.mapped("state"), ["draft", "draft"])
        self.assertEqual(sum(family.mapped("total_amount_currency")), 160)
        self.assertFalse(family.account_move_id)

    def test_agent_native_submit_approve_reset_and_refuse_keep_their_authority(self):
        agent = self._agent()
        expense = self._expense(120)
        self._operation(expense, agent, "action_submit").action_submit()
        self.assertEqual(expense.state, "submitted")
        self._operation(expense, agent, "action_approve").action_approve()
        self.assertEqual(expense.state, "approved")
        self._operation(expense, agent, "action_reset").action_reset()
        self.assertEqual(expense.state, "draft")
        self._operation(expense, agent, "action_submit").action_submit()
        action = self._operation(expense, agent, "action_refuse").action_refuse()
        wizard = self.env[action["res_model"]].with_user(agent.user_id).create({
            "reason": "[QA] Request corrected evidence", "expense_ids": [Command.set(expense.ids)],
        })
        self._operation(wizard, agent, "action_refuse").action_refuse()
        self.assertEqual(expense.state, "refused")
        self.assertFalse(expense.account_move_id)

    def test_agent_native_employee_and_company_posting_keep_their_entries(self):
        agent = self._agent()
        for amount, payer in ((121, "own_account"), (122, "company_account")):
            expense = self._expense(amount, payer=payer)
            self._operation(expense, agent, "action_submit").action_submit()
            self._operation(expense, agent, "action_approve").action_approve()
            action = self._operation(expense, agent, "action_post").action_post()
            if payer == "own_account":
                wizard = self.env[action["res_model"]].browse(action["res_id"])
                self._operation(wizard, agent, "action_post_entry", active_ids=expense.ids).action_post_entry()
            expense.invalidate_recordset()
            self.assertTrue(expense.account_move_id)
            self.assertEqual(expense.account_move_id.state, "posted")
            self.assertEqual(expense.total_amount_currency, amount)
            if payer == "own_account":
                self.assertEqual(expense.state, "posted")
                pay = self._operation(expense, agent, "action_pay").action_pay()
                self.assertEqual(pay["res_model"], "account.payment.register")
            else:
                self.assertTrue(expense.account_move_id.origin_payment_id)

    def test_agent_native_batch_add_submit_approve_return_restores_baseline(self):
        agent = self._agent()
        expense = self._expense(123)
        baseline = expense.account_id
        override = self.env["account.account"].create({"name": "[QA] Batch override", "code": "QA6111", "account_type": "expense"})
        batch = self.env["usl.expense.batch"].with_user(self.owner).create({
            "name": "[QA] Native Agent batch", "purpose": "[QA] Review travel context",
            "company_id": self.company.id, "employee_id": self.expense_employee.id,
            "account_override_id": override.id,
        })
        self._operation(batch, agent, "add_expenses").add_expenses(expense.ids)
        self.assertEqual(expense.expense_batch_id, batch)
        self._operation(batch, agent, "apply_context").apply_context()
        self.assertEqual(expense.account_id, override)
        self._operation(batch, agent, "action_submit").action_submit()
        self._operation(batch, agent, "action_approve").action_approve()
        self.assertEqual(expense.state, "approved")
        self._operation(expense, agent, "action_return_from_batch").action_return_from_batch()
        self.assertEqual(expense.state, "draft")
        self.assertFalse(expense.expense_batch_id)
        self.assertEqual(expense.account_id, baseline)

    def test_agent_native_approved_bank_match_keeps_validated_conversion(self):
        agent = self._agent()
        expense = self._expense(124)
        self._operation(expense, agent, "action_submit").action_submit()
        self._operation(expense, agent, "action_approve").action_approve()
        statement = self.env["account.bank.statement"].create({"journal_id": self.bank.id, "date": expense.date,
                                                                  "name": "[QA] Bank match"})
        bank_line = self.env["account.bank.statement.line"].create({
            "payment_ref": expense.name, "journal_id": self.bank.id, "statement_id": statement.id,
            "amount": -124, "date": expense.date,
        })
        self._operation(expense, agent, "action_refresh_bank_match_candidates").action_refresh_bank_match_candidates()
        candidate = expense.usl_bank_match_candidate_ids.filtered(lambda row: row.bank_statement_line_id == bank_line)
        self.assertEqual(len(candidate), 1)
        action = self._operation(candidate, agent, "action_open_confirmation").action_open_confirmation()
        wizard = self.env[action["res_model"]].browse(action["res_id"])
        self._operation(wizard, agent, "action_confirm").action_confirm()
        expense.invalidate_recordset()
        self.assertEqual(expense.payment_mode, "company_account")
        self.assertEqual(expense.payment_method_line_id, self.method)
        self.assertEqual(expense.total_amount_currency, 124)
        self.assertTrue(expense.account_move_id)
        self.assertEqual(expense.account_move_id.state, "posted")
        self.assertTrue(bank_line.is_reconciled)
