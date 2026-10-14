from datetime import timedelta
import json
from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged
from odoo.tests.common import new_test_user
from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged("post_install", "-at_install", "usl_accounting_vendor_bill_merge", "usl_accounting_unit")
class TestVendorBillMerge(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.merge_user = new_test_user(
            cls.env, login="vendor-bill-merge-user",
            groups="account.group_account_invoice",
            company_id=cls.company_data["company"].id,
            company_ids=[Command.set(cls.company_data["company"].ids)],
        )
        cls.day = fields.Date.today() - timedelta(days=2)

    def _bill(self, *, amount=24.0, partner=None, ref="[QA] Duplicate supplier bill", empty=False):
        bill = self._create_invoice(
            move_type="in_invoice",
            partner_id=partner or self.partner_a,
            company_id=self.company_data["company"],
            journal_id=self.company_data["default_journal_purchase"],
            currency_id=self.company_data["company"].currency_id,
            invoice_date=self.day, date=self.day, ref=ref,
            invoice_line_ids=[] if empty else [self._prepare_invoice_line(
                name="[QA] Synthetic duplicate service",
                account_id=self.company_data["default_account_expense"],
                quantity=1.0, price_unit=amount, tax_ids=self.env["account.tax"],
            )], post=False,
        )
        return bill.with_user(self.merge_user).with_context(
            allowed_company_ids=self.company_data["company"].ids,
        )

    def _conflict_wizard(self, bills):
        action = bills.action_merge_vendor_bills()
        self.assertEqual(action["res_model"], "usl.vendor.bill.merge.wizard")
        wizard = bills.env[action["res_model"]].browse(action["res_id"])
        self.assertFalse(wizard.winning_bill_id, "A conflict must require an explicit winning bill.")
        return wizard

    def test_empty_same_supplier_merges_without_a_prompt(self):
        first = self._bill(empty=True, ref="[QA] First empty upload")
        second = self._bill(empty=True, ref="[QA] Second empty upload")
        action = (second | first).action_merge_vendor_bills()
        self.assertEqual(action["res_model"], "account.move")
        self.assertEqual(action["res_id"], first.id)
        self.assertEqual(first.state, "draft")
        self.assertEqual(second.state, "cancel")
        self.assertEqual(second.vendor_bill_merge_target_id, first)
        self.assertEqual(first.amount_total, 0)
        self.assertFalse(first.invoice_line_ids)

    def test_identical_bills_keep_one_amount_and_original_winning_lines(self):
        first, second = self._bill(), self._bill()
        winning_lines = first.invoice_line_ids.ids
        action = (second | first).action_merge_vendor_bills()
        self.assertEqual(action["res_id"], first.id)
        self.assertEqual(first.amount_total, 24.0, "Duplicate quantity must never be added.")
        self.assertEqual(first.invoice_line_ids.ids, winning_lines)
        self.assertEqual(first.invoice_line_ids.quantity, 1.0)
        self.assertEqual(second.state, "cancel")
        self.assertTrue(second.invoice_line_ids.exists(), "Original bill lines remain in history.")
        self.assertEqual(second.amount_total, 24.0)

    def test_conflict_preview_leaves_both_bills_unchanged(self):
        first, second = self._bill(), self._bill(amount=47.0)
        before = [(bill.id, bill.state, bill.amount_total, bill.write_date) for bill in (first | second)]
        wizard = self._conflict_wizard(first | second)
        self.assertEqual(set(wizard.bill_ids.ids), set((first | second).ids))
        self.assertEqual([(bill.id, bill.state, bill.amount_total, bill.write_date) for bill in (first | second)], before)

    def test_conflict_keeps_chosen_bill_and_marks_native_to_review(self):
        first, second = self._bill(), self._bill(amount=47.0)
        winning_lines = second.invoice_line_ids.ids
        wizard = self._conflict_wizard(first | second)
        wizard.winning_bill_id = second
        action = wizard.action_merge()
        self.assertEqual(action["res_id"], second.id)
        self.assertEqual(second.amount_total, 47.0)
        self.assertEqual(second.invoice_line_ids.ids, winning_lines)
        self.assertEqual(second.review_state, "todo")
        self.assertEqual(first.state, "cancel")
        self.assertEqual(first.vendor_bill_merge_target_id, second)

    def test_different_supplier_is_a_choice_instead_of_silent_retargeting(self):
        first, second = self._bill(), self._bill(partner=self.partner_b)
        wizard = self._conflict_wizard(first | second)
        wizard.winning_bill_id = second
        wizard.action_merge()
        self.assertEqual(second.partner_id, self.partner_b)
        self.assertEqual(first.partner_id, self.partner_a)
        self.assertEqual(second.review_state, "todo")

    def test_original_attachments_remain_owned_by_their_original_bill(self):
        first, second = self._bill(), self._bill()
        attachment = self.env["ir.attachment"].create({
            "name": "[QA] Original duplicate evidence.txt",
            "raw": b"Invented disposable duplicate evidence",
            "res_model": "account.move", "res_id": second.id,
        })
        (first | second).action_merge_vendor_bills()
        self.assertTrue(second.exists())
        self.assertTrue(attachment.exists())
        self.assertEqual((attachment.res_model, attachment.res_id), ("account.move", second.id))
        self.assertEqual(attachment.raw.content, b"Invented disposable duplicate evidence")
        self.assertEqual(second.vendor_bill_merge_target_id, first)

    def test_stale_conflict_choice_cannot_cancel_a_changed_bill(self):
        first, second = self._bill(), self._bill(amount=47.0)
        wizard = self._conflict_wizard(first | second)
        wizard.winning_bill_id = second
        first.invoice_line_ids.price_unit = 26.0
        with self.assertRaises(UserError):
            wizard.action_merge()
        self.assertEqual((first | second).mapped("state"), ["draft", "draft"])
        self.assertEqual(first.amount_total, 26.0)
        self.assertFalse((first | second).mapped("vendor_bill_merge_target_id"))

    def test_winner_must_be_one_of_the_reviewed_bills(self):
        first, second, unrelated = self._bill(), self._bill(amount=47.0), self._bill(amount=80.0)
        wizard = self._conflict_wizard(first | second)
        with self.assertRaises(UserError):
            wizard.write({"winning_bill_id": unrelated.id})
            wizard.action_merge()
        self.assertEqual((first | second | unrelated).mapped("state"), ["draft", "draft", "draft"])

    def test_single_bill_does_not_change_state(self):
        bill = self._bill()
        with self.assertRaises(UserError):
            bill.action_merge_vendor_bills()
        self.assertEqual(bill.state, "draft")
        self.assertFalse(bill.vendor_bill_merge_target_id)

    def test_readonly_actor_cannot_merge_bills(self):
        first, second = self._bill(), self._bill()
        reader = new_test_user(self.env, login="vendor-bill-merge-reader",
            groups="account.group_account_readonly",
            company_id=self.company_data["company"].id,
            company_ids=[Command.set(self.company_data["company"].ids)])
        with self.assertRaises(AccessError):
            (first | second).with_user(reader).action_merge_vendor_bills()
        self.assertEqual((first | second).mapped("state"), ["draft", "draft"])

    def test_posted_identical_bills_keep_winner_posted_without_adding_quantity(self):
        first, second = self._bill(), self._bill()
        (first | second).action_post()
        lines = first.line_ids.ids
        action = (second | first).action_merge_vendor_bills()
        self.assertEqual(action["res_id"], first.id)
        self.assertEqual(first.state, "posted")
        self.assertEqual(first.line_ids.ids, lines)
        self.assertEqual(first.amount_total, 24.0)
        self.assertEqual(second.state, "cancel")
        self.assertEqual(second.amount_total, 24.0)

    def test_mixed_draft_posted_requires_explicit_winner(self):
        first, second = self._bill(), self._bill()
        second.action_post()
        wizard = self._conflict_wizard(first | second)
        wizard.winning_bill_id = second
        self.assertEqual(wizard.winning_state, "posted")
        wizard.action_merge()
        self.assertEqual(second.state, "posted")
        self.assertEqual(first.state, "cancel")
        self.assertEqual(second.review_state, "todo")

    def test_reset_to_draft_clears_active_lineage_without_resetting_winner(self):
        first, second = self._bill(), self._bill()
        (first | second).action_merge_vendor_bills()
        second.button_draft()
        self.assertFalse(second.vendor_bill_merge_target_id)
        self.assertEqual(first.state, "draft")
        second.button_cancel()
        self.assertFalse(second.vendor_bill_merge_target_id)
        self.assertFalse(first._vendor_bill_merge_originals())

    def test_copy_does_not_inherit_merge_lineage(self):
        first, second = self._bill(), self._bill()
        (first | second).action_merge_vendor_bills()
        copied = second.copy()
        self.assertFalse(copied.vendor_bill_merge_target_id)
        self.assertEqual(copied.state, "draft")

    def test_chain_preserves_edges_and_accessible_original_navigation(self):
        first, second, third = self._bill(), self._bill(), self._bill(amount=47)
        (first | second).action_merge_vendor_bills()
        wizard = self._conflict_wizard(first | third)
        wizard.winning_bill_id = third
        wizard.action_merge()
        self.assertEqual(second.vendor_bill_merge_target_id, first)
        self.assertEqual(first.vendor_bill_merge_target_id, third)
        self.assertEqual(set(third._vendor_bill_merge_originals().ids), {first.id, second.id})
        first.button_draft()
        self.assertFalse(first.vendor_bill_merge_target_id)
        self.assertEqual(second.vendor_bill_merge_target_id, first)
        self.assertFalse(third._vendor_bill_merge_originals())

    def test_public_lineage_write_and_serializable_default_are_rejected(self):
        first, second = self._bill(), self._bill()
        with self.assertRaises(AccessError):
            second.write({"vendor_bill_merge_target_id": first.id})
        with self.assertRaises(AccessError):
            second.with_context(default_vendor_bill_merge_target_id=first.id).copy()
        self.assertFalse(second.vendor_bill_merge_target_id)

    def test_public_wizard_initialization_and_selection_forgery_are_rejected(self):
        first, second = self._bill(), self._bill(amount=47)
        with self.assertRaises(AccessError):
            first.env["usl.vendor.bill.merge.wizard"].with_context(usl_vendor_bill_merge_init=True).create({
                "bill_ids": [Command.set((first | second).ids)], "snapshot": "forged",
            })
        wizard = self._conflict_wizard(first | second)
        with self.assertRaises(AccessError):
            wizard.write({"snapshot": "forged"})
        with self.assertRaises(AccessError):
            wizard.write({"bill_ids": [Command.set(first.ids)]})

    def test_late_audit_failure_rolls_back_cancellation_and_review(self):
        first, second = self._bill(), self._bill(amount=47)
        wizard = self._conflict_wizard(first | second)
        wizard.winning_bill_id = second
        before_messages = (first | second).message_ids.ids
        with patch.object(type(first), "message_post", side_effect=UserError("Invented late audit failure")):
            with self.assertRaises(UserError):
                wizard.action_merge()
        self.assertEqual((first | second).mapped("state"), ["draft", "draft"])
        self.assertFalse(first.vendor_bill_merge_target_id)
        self.assertNotEqual(second.review_state, "todo")
        self.assertEqual((first | second).message_ids.ids, before_messages)

    def test_native_payment_hold_without_payment_stays_eligible(self):
        first, second = self._bill(), self._bill()
        (first | second).action_post()
        first.action_toggle_block_payment()
        second.action_toggle_block_payment()
        self.assertEqual(first.payment_state, "blocked")
        action = (first | second).action_merge_vendor_bills()
        self.assertEqual(action["res_id"], first.id)
        self.assertEqual(first.payment_state, "blocked")
        self.assertEqual(second.state, "cancel")

    def test_recurring_next_draft_is_preserved_and_merge_refused(self):
        first, second = self._bill(), self._bill()
        (first | second).action_post()
        following = self._bill()
        second.auto_post_origin_id = first
        following.write({"auto_post_origin_id": first.id, "date": self.day + timedelta(days=1)})
        wizard = self._conflict_wizard(first | second)
        wizard.winning_bill_id = first
        with self.assertRaises(UserError):
            wizard.action_merge()
        self.assertTrue(following.exists())
        self.assertEqual(following.state, "draft")
        self.assertEqual((first | second).mapped("state"), ["posted", "posted"])

    def test_recurring_next_posted_does_not_block_later_draft(self):
        first, second = self._bill(), self._bill()
        (first | second).action_post()
        following, later = self._bill(), self._bill()
        following.write({"date": self.day + timedelta(days=1), "invoice_date": self.day + timedelta(days=1)})
        following.action_post()
        second.auto_post_origin_id = first
        following.auto_post_origin_id = first
        later.write({"auto_post_origin_id": first.id, "date": self.day + timedelta(days=2)})
        wizard = self._conflict_wizard(first | second)
        wizard.winning_bill_id = first
        wizard.action_merge()
        self.assertEqual(second.state, "cancel")
        self.assertEqual(following.state, "posted")
        self.assertEqual(later.state, "draft")
        self.assertTrue(later.exists())

    def test_conflict_preview_never_writes_persistent_bill_or_lines(self):
        first, second = self._bill(), self._bill(amount=47)
        with patch.object(type(first), "write", side_effect=AssertionError("Preview wrote a bill")), \
             patch.object(type(first.line_ids), "write", side_effect=AssertionError("Preview wrote a line")):
            self._conflict_wizard(first | second)

    def test_changed_original_evidence_invalidates_conflict_choice(self):
        first, second = self._bill(), self._bill(amount=47)
        wizard = self._conflict_wizard(first | second)
        wizard.winning_bill_id = second
        self.env["ir.attachment"].create({"name": "[QA] Later evidence.txt", "raw": b"Invented later evidence",
            "res_model": "account.move", "res_id": first.id})
        with self.assertRaises(UserError):
            wizard.action_merge()
        self.assertEqual((first | second).mapped("state"), ["draft", "draft"])

    def test_equal_total_different_account_requires_choice(self):
        first, second = self._bill(), self._bill()
        second.invoice_line_ids.account_id = self.company_data["default_account_expense"].copy({
            "name": "[QA] Other expense classification", "code": "QA2771OTHER",
        })
        self.assertEqual(first.amount_total, second.amount_total)
        self._conflict_wizard(first | second)

    def test_paid_reconciled_bills_refused_without_changing_native_payment(self):
        first, second = self._bill(), self._bill()
        (first | second).action_post()
        payment = self.env["account.payment.register"].with_context(
            active_model="account.move", active_ids=first.ids,
        ).create({"payment_date": self.day, "journal_id": self.company_data["default_journal_bank"].id})
        payment.action_create_payments()
        before = first.line_ids.matched_debit_ids | first.line_ids.matched_credit_ids
        with self.assertRaises(UserError):
            (first | second).action_merge_vendor_bills()
        self.assertEqual((first | second).mapped("state"), ["posted", "posted"])
        self.assertEqual(first.line_ids.matched_debit_ids | first.line_ids.matched_credit_ids, before)

    def test_selected_company_switch_cannot_expand_merge_access(self):
        first, second = self._bill(), self._bill()
        other = self.env["res.company"].create({"name": "[QA] Unselected merge company"})
        with self.assertRaises(AccessError):
            (first | second).with_context(allowed_company_ids=other.ids).action_merge_vendor_bills()
        self.assertEqual((first | second).mapped("state"), ["draft", "draft"])

    def test_tax_and_analytic_facts_require_choice_even_with_same_total(self):
        first, second = self._bill(), self._bill()
        tax = self.env["account.tax"].create({"name": "[QA] Zero-rate distinction", "amount": 0,
            "type_tax_use": "purchase", "company_id": first.company_id.id})
        second.invoice_line_ids.tax_ids = tax
        self.assertEqual(first.amount_total, second.amount_total)
        self._conflict_wizard(first | second)
        second.invoice_line_ids.tax_ids = False
        plan = self.env["account.analytic.plan"].create({"name": "[QA] Merge allocation"})
        analytic = self.env["account.analytic.account"].create({"name": "[QA] Other attribution", "plan_id": plan.id})
        second.invoice_line_ids.analytic_distribution = {str(analytic.id): 100.0}
        self.assertEqual(first.amount_total, second.amount_total)
        self._conflict_wizard(first | second)

    def test_review_todo_visible_in_ordinary_billing_draft_views(self):
        first, second = self._bill(), self._bill(amount=47)
        wizard = self._conflict_wizard(first | second)
        wizard.winning_bill_id = second
        wizard.action_merge()
        from lxml import etree
        arch = etree.fromstring(second.get_view(view_id=self.env.ref("account.view_move_form").id, view_type="form")["arch"])
        badges = arch.xpath("//header/field[@name='review_state'][@widget='badge'][@readonly='1']")
        self.assertTrue(badges, "Billing users must see the native draft To Review result.")

    def test_late_native_cancellation_failure_restores_all_selected_bills(self):
        first, second, third = self._bill(), self._bill(), self._bill()
        native_cancel = type(first).button_cancel
        def cancel_then_fail(records):
            native_cancel(records[:1])
            raise UserError("Invented native cancellation refusal")
        with patch.object(type(first), "button_cancel", cancel_then_fail):
            with self.assertRaises(UserError):
                (first | second | third).action_merge_vendor_bills()
        self.assertEqual((first | second | third).mapped("state"), ["draft"] * 3)
        self.assertFalse((first | second | third).mapped("vendor_bill_merge_target_id"))

    def test_colliding_native_names_choose_visibly_without_merging(self):
        first, second = self._bill(), self._bill(amount=47)
        self.assertEqual(first.display_name, second.display_name)
        wizard = self._conflict_wizard(first | second)
        action = second.with_context(vendor_bill_merge_wizard_id=wizard.id).action_choose_vendor_bill_merge_winner()
        self.assertEqual(action["res_id"], wizard.id)
        self.assertEqual(wizard.winning_bill_id, second)
        self.assertIn(str(second.id), wizard.winning_details)
        self.assertIn("47", wizard.winning_details)
        self.assertEqual((first | second).mapped("state"), ["draft", "draft"])
        self.assertFalse((first | second).mapped("vendor_bill_merge_target_id"))
        selected = first.with_context(vendor_bill_merge_wizard_id=wizard.id)
        selected.action_choose_vendor_bill_merge_winner()
        self.assertEqual(wizard.winning_bill_id, first)
        self.assertIn("24", wizard.winning_details)
        wizard.action_merge()
        self.assertEqual(first.amount_total, 24)
        self.assertEqual(second.state, "cancel")

    def test_row_choice_cannot_select_outside_reviewed_bill_ids(self):
        first, second, outsider = self._bill(), self._bill(amount=47), self._bill(amount=80)
        wizard = self._conflict_wizard(first | second)
        with self.assertRaises(UserError):
            outsider.with_context(vendor_bill_merge_wizard_id=wizard.id).action_choose_vendor_bill_merge_winner()
        self.assertFalse(wizard.winning_bill_id)
        self.assertEqual((first | second | outsider).mapped("state"), ["draft"] * 3)

    def test_row_choice_cannot_use_another_creators_dialog(self):
        first, second = self._bill(), self._bill(amount=47)
        other = new_test_user(self.env, login="qa-other-merge-creator",
            groups="account.group_account_invoice", company_id=first.company_id.id,
            company_ids=[Command.set(first.company_id.ids)])
        wizard = self._conflict_wizard((first | second).with_user(other))
        with self.assertRaises(AccessError):
            first.with_context(vendor_bill_merge_wizard_id=wizard.id).action_choose_vendor_bill_merge_winner()
        self.assertFalse(wizard.winning_bill_id)
        self.assertEqual((first | second).mapped("state"), ["draft", "draft"])

    def _merge_agent(self, mode, label):
        if "usl.agent" not in self.env:
            self.skipTest("Managed Agent extension is qualified separately in the full distribution registry.")
        return self.env["usl.agent"].with_user(self.merge_user).create({
            "name": "[QA] Merge " + label, "purpose": "[QA] Native bill merge authority",
            "owner_id": self.merge_user.id, "company_id": self.merge_user.company_id.id,
            "company_ids": [Command.set(self.merge_user.company_ids.ids)],
            "delegated_group_ids": [Command.set(self.env.ref("account.group_account_invoice").ids)],
            "access_mode": mode,
        })

    def test_managed_agent_creator_owner_ceiling_and_sibling_boundaries(self):
        first, second = self._bill(), self._bill(amount=47)
        agent = self._merge_agent("read_write", "creator")
        sibling = self._merge_agent("read_write", "sibling")
        bills = (first | second).with_user(agent.user_id)
        wizard = self._conflict_wizard(bills)
        self.assertEqual(wizard.create_uid, agent.user_id)
        self.assertEqual(wizard.accountable_user_id, self.merge_user)
        self.assertEqual(wizard.with_user(self.merge_user).read(["id"])[0]["id"], wizard.id)
        with self.assertRaises(AccessError):
            wizard.with_user(self.merge_user).write({"winning_bill_id": second.id})
        with self.assertRaises(AccessError):
            second.with_user(sibling.user_id).with_context(
                vendor_bill_merge_wizard_id=wizard.id,
            ).action_choose_vendor_bill_merge_winner()
        second.with_user(agent.user_id).with_context(
            vendor_bill_merge_wizard_id=wizard.id,
        ).action_choose_vendor_bill_merge_winner()
        wizard.action_merge()
        self.assertEqual(first.state, "cancel")
        self.assertEqual(second.amount_total, 47)
        self.assertEqual(second.review_state, "todo")

    def test_readonly_managed_agent_cannot_merge(self):
        first, second = self._bill(), self._bill()
        agent = self._merge_agent("read_only", "reader")
        with self.assertRaises(AccessError):
            (first | second).with_user(agent.user_id).action_merge_vendor_bills()
        self.assertEqual((first | second).mapped("state"), ["draft", "draft"])

    def test_native_server_agent_scope_never_leaves_returned_bill_action(self):
        first, second = self._bill(), self._bill()
        agent = self._merge_agent("read_write", "serialized action")
        from odoo.addons.usl_access_control.models.agent_policy_tokens import (
            AGENT_OPERATION_SCOPE_CONTEXT_KEY, create_agent_operation_scope,
        )
        scoped = (first | second).with_user(agent.user_id).with_context(**{
            AGENT_OPERATION_SCOPE_CONTEXT_KEY: create_agent_operation_scope(
                agent_user_id=agent.user_id.id, root_model="account.move",
                root_method="action_merge_vendor_bills", access="write",
            ),
        })
        action = scoped.action_merge_vendor_bills()
        json.dumps(action)
        self.assertNotIn(AGENT_OPERATION_SCOPE_CONTEXT_KEY, action["context"])
        self.assertEqual(action["context"]["allowed_company_ids"], first.company_id.ids)
        self.assertEqual(action["res_id"], first.id)

    def test_different_native_origins_require_explicit_choice(self):
        first, second = self._bill(), self._bill()
        first.invoice_origin = "[QA] First acquisition"
        second.invoice_origin = "[QA] Second acquisition"
        self._conflict_wizard(first | second)

    def test_kept_recurring_winner_preserves_its_future_draft(self):
        first, second = self._bill(), self._bill()
        (first | second).action_post()
        following = self._bill()
        first.auto_post_origin_id = first
        following.write({"auto_post_origin_id": first.id, "date": self.day + timedelta(days=1)})
        wizard = self._conflict_wizard(first | second)
        wizard.winning_bill_id = first
        wizard.action_merge()
        self.assertEqual(first.state, "posted")
        self.assertEqual(second.state, "cancel")
        self.assertTrue(following.exists())
        self.assertEqual(following.state, "draft")

    def test_managed_agent_posted_merge_preserves_financial_lines_and_distribution(self):
        first, second = self._bill(), self._bill()
        plan = self.env["account.analytic.plan"].create({"name": "[QA] Posted merge allocation"})
        analytic = self.env["account.analytic.account"].create({"name": "[QA] Posted purpose", "plan_id": plan.id})
        distribution = {str(analytic.id): 100.0}
        (first | second).invoice_line_ids.write({"analytic_distribution": distribution})
        (first | second).action_post()
        first_lines, second_lines = first.line_ids.ids, second.line_ids.ids
        self.assertTrue(second.line_ids.analytic_line_ids)
        agent = self._merge_agent("read_write", "posted analytics")
        (first | second).with_user(agent.user_id).action_merge_vendor_bills()
        self.assertEqual(first.state, "posted")
        self.assertEqual(second.state, "cancel")
        self.assertEqual(first.line_ids.ids, first_lines)
        self.assertEqual(second.line_ids.ids, second_lines)
        self.assertEqual(first.invoice_line_ids.analytic_distribution, distribution)
        self.assertEqual(second.invoice_line_ids.analytic_distribution, distribution)
        self.assertFalse(second.line_ids.analytic_line_ids, "Native cancelled projections are derived, not original financial lines.")
