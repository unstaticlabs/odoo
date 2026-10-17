from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests import tagged
from odoo.addons.hr_expense.tests.common import TestExpenseCommon

from ..controllers.json2 import UslAgentJson2Controller
from ..models.agent_policy_tokens import AGENT_OPERATION_SCOPE_CONTEXT_KEY


@tagged('post_install', '-at_install', 'usl_agent_receipt_copy')
class TestAgentReceiptCopy(TestExpenseCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        groups = (cls.env.ref('account.group_account_manager')
                  | cls.env.ref('hr_expense.group_hr_expense_manager')
                  | cls.env.ref('project.group_project_manager'))
        cls.env.user.group_ids |= groups
        cls.agent = cls.env['usl.agent'].create({
            'name': 'Native receipt-copy Agent',
            'purpose': 'Complete delegated native posting with its receipts.',
            'owner_id': cls.env.user.id,
            'company_id': cls.env.company.id,
            'company_ids': [Command.set(cls.env.company.ids)],
            'delegated_group_ids': [Command.set(groups.ids)],
            'read_only_group_ids': [Command.clear()],
            'access_mode': 'read_write',
        })
        cls.project = cls.env['project.project'].create({
            'name': 'Synthetic attachment project', 'company_id': cls.env.company.id,
        })

    def _scope(self, model='project.project', method='write'):
        access = self.agent._api_method_access(model, method)
        self.assertEqual(access, 'write')
        return UslAgentJson2Controller._agent_call_context(
            context={'allowed_company_ids': self.env.company.ids},
            agent=self.agent, model_name=model, method_name=method, access=access,
        )

    def _attachments(self, context=None):
        return self.env['ir.attachment'].with_user(self.agent.user_id).with_context(context or {})

    def _values(self, record=None):
        record = record or self.project
        return {'name': 'Synthetic receipt.txt', 'raw': b'Invented QA receipt',
                'res_model': record._name, 'res_id': record.id}

    def test_native_project_attachment_keeps_ordinary_access_checks(self):
        attachment = self._attachments(self._scope()).create(self._values())
        self.assertEqual(attachment.res_id, self.project.id)
        self.assertEqual(bytes(attachment.raw), b'Invented QA receipt')
        self.assertEqual(attachment.create_uid, self.agent.user_id)
        self.assertFalse(attachment.env.su)

    def test_missing_or_forged_scope_cannot_create_attachment(self):
        for context in ({}, {AGENT_OPERATION_SCOPE_CONTEXT_KEY: 'write'}):
            with self.subTest(context=context), self.assertRaises(AccessError):
                self._attachments(context).create(self._values())
        self.assertIsNone(self.agent._api_method_access('ir.attachment', 'create'))

    def test_scope_does_not_cross_company_boundary(self):
        other = self.env['project.project'].create({
            'name': 'Other company attachment project',
            'company_id': self.company_data_2['company'].id,
        })
        with self.assertRaises(AccessError):
            self._attachments(self._scope()).create(self._values(other))

    def test_scope_does_not_bypass_owner_record_access(self):
        project_group = self.env.ref('project.group_project_manager')
        scope = self._scope()
        self.agent.owner_id.group_ids -= project_group
        self.assertFalse(self.project.with_user(self.agent.owner_id).has_access("write"))
        with self.assertRaises(AccessError):
            self._attachments(scope).create(self._values())

    def test_readonly_delegation_does_not_gain_attachment_creation(self):
        self.agent.with_user(self.agent.owner_id).write({
            'read_only_group_ids': [Command.set(self.agent.delegated_group_ids.ids)],
        })
        self.assertIsNone(self.agent._api_method_access('project.project', 'write'))
        with self.assertRaises(AccessError):
            self._attachments().create(self._values())

    def test_native_company_posting_copies_receipt_without_sudo(self):
        bank = self.company_data['default_journal_bank']
        method = bank.outbound_payment_method_line_ids[:1]
        self.assertTrue(method)
        if not method.payment_account_id:
            method.payment_account_id = self.env['account.chart.template'].ref(
                'account_journal_payment_credit_account_id')
        self.env.company.company_expense_allowed_payment_method_line_ids = [Command.set(method.ids)]
        expense = self.create_expenses({
            'name': 'Synthetic delegated company expense',
            'payment_mode': 'company_account',
            'payment_method_line_id': method.id,
            'tax_ids': [Command.clear()],
        })
        receipt = self.env['ir.attachment'].create(self._values(expense))
        expense.message_main_attachment_id = receipt
        expense.action_submit()
        expense._do_approve()
        delegated = expense.with_user(self.agent.user_id).with_context(
            self._scope('hr.expense', 'action_post'))
        delegated.action_post()
        self.assertEqual(expense.state, 'paid')
        self.assertEqual(expense.account_move_id.state, 'posted')
        copies = expense.account_move_id.attachment_ids
        self.assertEqual(len(copies), 1)
        self.assertEqual(bytes(copies.raw), bytes(receipt.raw))
        self.assertEqual(copies.create_uid, self.agent.user_id)
        self.assertTrue(receipt.exists())
