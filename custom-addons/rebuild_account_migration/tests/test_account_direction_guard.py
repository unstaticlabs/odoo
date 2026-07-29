from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "rebuild_account_direction_guard")
class TestAccountDirectionGuard(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.journal = cls.env["account.journal"].search(
            [
                ("company_id", "=", cls.company.id),
                ("type", "=", "general"),
                ("id", "!=", cls.company.currency_exchange_journal_id.id),
            ],
            limit=1,
        )
        cls.counterpart = cls.env["account.account"].create(
            {
                "code": "T471991",
                "name": "Direction Guard Counterpart",
                "account_type": "asset_current",
                "company_ids": [Command.set(cls.company.ids)],
            },
        )
        cls.loss_account = cls.env["account.account"].create(
            {
                "code": "666991",
                "name": "Protected Exchange Loss",
                "account_type": "expense",
                "company_ids": [Command.set(cls.company.ids)],
            },
        )
        cls.gain_account = cls.env["account.account"].create(
            {
                "code": "766991",
                "name": "Protected Exchange Gain",
                "account_type": "income",
                "company_ids": [Command.set(cls.company.ids)],
            },
        )

    def _move(self, account, *, wrong_side):
        expected = account._rebuild_expected_entry_direction()
        protected_debit = 100.0 if expected == "debit" else 0.0
        protected_credit = 100.0 if expected == "credit" else 0.0
        if wrong_side:
            protected_debit, protected_credit = protected_credit, protected_debit
        return self.env["account.move"].create(
            {
                "move_type": "entry",
                "journal_id": self.journal.id,
                "date": "2026-07-29",
                "line_ids": [
                    Command.create(
                        {
                            "name": "Protected direction",
                            "account_id": account.id,
                            "debit": protected_debit,
                            "credit": protected_credit,
                        },
                    ),
                    Command.create(
                        {
                            "name": "Counterpart",
                            "account_id": self.counterpart.id,
                            "debit": protected_credit,
                            "credit": protected_debit,
                        },
                    ),
                ],
            },
        )

    def test_666_and_766_automatic_directions(self):
        self.assertEqual(
            self.loss_account._rebuild_expected_entry_direction(),
            "debit",
        )
        self.assertEqual(
            self.gain_account._rebuild_expected_entry_direction(),
            "credit",
        )
        unrelated = self.env["account.account"].create(
            {
                "code": "625991",
                "name": "Unprotected Expense",
                "account_type": "expense",
                "company_ids": [Command.set(self.company.ids)],
            },
        )
        self.assertFalse(unrelated._rebuild_expected_entry_direction())

    def test_wrong_side_requires_current_confirmation(self):
        move = self._move(self.loss_account, wrong_side=True)
        self.assertTrue(move.rebuild_direction_exception_required)
        self.assertFalse(move.rebuild_direction_exception_confirmed)
        with self.assertRaisesRegex(UserError, "Check the account direction"):
            move.action_post()

        move.action_rebuild_confirm_direction_exception()
        self.assertTrue(move.rebuild_direction_exception_confirmed)

        protected_line = move.line_ids.filtered(
            lambda line: line.account_id == self.loss_account,
        )
        counterpart_line = move.line_ids - protected_line
        protected_line.with_context(check_move_validity=False).credit = 125.0
        counterpart_line.with_context(check_move_validity=False).debit = 125.0
        self.assertFalse(move.rebuild_direction_exception_confirmed)
        with self.assertRaisesRegex(UserError, "Check the account direction"):
            move.action_post()

        move.action_rebuild_confirm_direction_exception()
        move.action_post()
        self.assertEqual(move.state, "posted")
        self.assertTrue(
            move.message_ids.filtered(
                lambda message: "Exceptional account direction confirmed"
                in (message.body or ""),
            ),
        )

    def test_normal_sides_post_without_confirmation(self):
        loss_move = self._move(self.loss_account, wrong_side=False)
        gain_move = self._move(self.gain_account, wrong_side=False)
        (loss_move | gain_move).action_post()
        self.assertEqual(loss_move.state, "posted")
        self.assertEqual(gain_move.state, "posted")

    def test_policy_can_be_disabled_explicitly(self):
        self.loss_account.rebuild_entry_direction_policy = "none"
        move = self._move(self.loss_account, wrong_side=True)
        move.action_post()
        self.assertEqual(move.state, "posted")

    def test_native_exchange_context_and_formal_reversal_are_exempt(self):
        generated_move = self._move(self.gain_account, wrong_side=True)
        generated_move.with_context(no_exchange_difference=True).action_post()
        self.assertEqual(generated_move.state, "posted")

        original = self._move(self.loss_account, wrong_side=False)
        original.action_post()
        reversal = original._reverse_moves()
        self.assertTrue(reversal.reversed_entry_id)
        reversal.action_post()
        self.assertEqual(reversal.state, "posted")

    def test_refunds_and_source_reconstruction_are_exempt(self):
        refund = self.env["account.move"].new({"move_type": "in_refund"})
        self.assertTrue(refund._rebuild_direction_guard_is_exempt())

        imported = self._move(self.loss_account, wrong_side=True)
        imported.rebuild_source_id = 999001
        imported.action_post()
        self.assertEqual(imported.state, "posted")

    def test_configuration_and_draft_warning_are_visible(self):
        account_arch = self.env.ref(
            "rebuild_account_migration.view_account_form_entry_direction_guard",
        ).arch_db
        move_arch = self.env.ref(
            "rebuild_account_migration.view_move_form_entry_direction_guard",
        ).arch_db
        self.assertIn("rebuild_entry_direction_policy", account_arch)
        self.assertIn("Confirm exceptional direction", move_arch)
