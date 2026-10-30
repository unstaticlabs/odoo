from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests import tagged
from odoo.addons.hr_expense.tests.common import TestExpenseCommon

from ..controllers.json2 import UslAgentJson2Controller


@tagged("post_install", "-at_install", "usl_agent_receipt_copy")
class TestAgentReceiptCopy(TestExpenseCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        groups = (cls.env.ref("account.group_account_manager")
                  | cls.env.ref("hr_expense.group_hr_expense_manager"))
        cls.env.user.group_ids |= groups
        cls.agent = cls.env["usl.agent"].create({
            "name": "Synthetic native receipt-copy Agent",
            "purpose": "Complete delegated native posting with its receipts.",
            "owner_id": cls.env.user.id,
            "company_id": cls.env.company.id,
            "company_ids": [Command.set(cls.env.company.ids)],
            "delegated_group_ids": [Command.set(groups.ids)],
            "read_only_group_ids": [Command.clear()],
            "access_mode": "read_write",
        })
        bank = cls.company_data["default_journal_bank"]
        method = bank.outbound_payment_method_line_ids[:1]
        if not method.payment_account_id:
            method.payment_account_id = cls.env["account.chart.template"].ref(
                "account_journal_payment_credit_account_id")
        cls.payment_method = method
        cls.env.company.company_expense_allowed_payment_method_line_ids = [Command.set(method.ids)]

    def _approved_expense_with_receipts(self):
        expense = self.create_expenses({
            "name": "Synthetic delegated company expense",
            "payment_mode": "company_account",
            "payment_method_line_id": self.payment_method.id,
            "tax_ids": [Command.clear()],
        })
        receipts = self.env["ir.attachment"].create([
            {"name": "Synthetic receipt.txt", "raw": b"Invented QA receipt\x00\xff",
             "res_model": expense._name, "res_id": expense.id},
            {"name": "Synthetic supporting receipt.txt", "raw": b"Invented supporting evidence",
             "res_model": expense._name, "res_id": expense.id},
        ])
        expense.message_main_attachment_id = receipts[0]
        expense.action_submit()
        expense._do_approve()
        return expense, receipts

    def _delegated_post(self, expense):
        access = UslAgentJson2Controller._check_agent_call(
            agent=self.agent, model_name="hr.expense", method_name="action_post", kwargs={},
        )
        context = UslAgentJson2Controller._agent_call_context(
            context={"allowed_company_ids": self.env.company.ids},
            agent=self.agent, model_name="hr.expense", method_name="action_post", access=access,
        )
        return expense.with_user(self.agent.user_id).with_context(context).action_post()

    def test_native_company_posting_preserves_receipt_evidence_and_actor(self):
        for delegated in (False, True):
            with self.subTest(delegated=delegated):
                expense, originals = self._approved_expense_with_receipts()
                before = {receipt.name: bytes(receipt.raw) for receipt in originals}
                original_metadata = originals.read(["res_model", "res_id", "checksum", "create_uid"])
                if delegated:
                    self._delegated_post(expense)
                else:
                    expense.action_post()
                self.assertEqual(expense.state, "paid")
                self.assertEqual(expense.account_move_id.state, "posted")
                copies = expense.account_move_id.attachment_ids
                self.assertEqual(len(copies), len(originals))
                self.assertFalse(copies & originals)
                self.assertEqual({copy.name: bytes(copy.raw) for copy in copies}, before)
                actor = self.agent.user_id if delegated else self.env.user
                self.assertEqual(copies.create_uid, actor)
                self.assertTrue(all(copy.res_model == "account.move" and
                                    copy.res_id == expense.account_move_id.id for copy in copies))
                self.assertEqual(originals.exists(), originals)
                self.assertEqual(originals.read(["res_model", "res_id", "checksum", "create_uid"]),
                                 original_metadata)
                self.assertEqual({receipt.name: bytes(receipt.raw) for receipt in originals}, before)
                self.assertEqual(expense.message_main_attachment_id, originals[0])

    def test_readonly_delegation_cannot_post_or_copy_receipts(self):
        expense, originals = self._approved_expense_with_receipts()
        self.agent.with_user(self.agent.owner_id).write({
            "read_only_group_ids": [Command.set(self.agent.delegated_group_ids.ids)],
        })
        with self.assertRaises(AccessError):
            self._delegated_post(expense)
        self.assertEqual(expense.state, "approved")
        self.assertFalse(expense.account_move_id)
        self.assertEqual(expense.attachment_ids, originals)
