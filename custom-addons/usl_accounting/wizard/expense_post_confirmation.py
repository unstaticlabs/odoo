from odoo import _, fields, models
from odoo.exceptions import UserError

from ..models.expense_bank_matching import _POSTING_REVIEW_CONFIRMED


class HrExpensePostWizard(models.TransientModel):
    _inherit = "hr.expense.post.wizard"

    usl_review_expense_ids = fields.Many2many("hr.expense", readonly=True)
    usl_review_candidate_ids = fields.Many2many("usl.expense.bank.match.candidate", readonly=True)
    usl_review_fingerprint = fields.Char(readonly=True)
    usl_review_stage = fields.Selection([
        ("submit", "Submit"), ("approve", "Approve"),
        ("post", "Post"), ("post_entry", "Post entry"),
    ], readonly=True)
    usl_review_changed = fields.Boolean(readonly=True)
    usl_review_completed = fields.Boolean(readonly=True)

    def _usl_open_employee_payment_review(self, expenses, candidates, stage, fingerprint):
        self.ensure_one()
        self.write({
            "usl_review_changed": bool(self.usl_review_fingerprint),
            "usl_review_expense_ids": [(6, 0, expenses.ids)],
            "usl_review_candidate_ids": [(6, 0, candidates.ids)],
            "usl_review_fingerprint": fingerprint,
            "usl_review_stage": stage,
        })
        return self._usl_review_action()

    def _usl_review_action(self):
        return {
            "type": "ir.actions.act_window", "name": _("Company payment match"),
            "res_model": self._name, "res_id": self.id, "view_mode": "form",
            "views": [(self.env.ref("usl_accounting.view_expense_post_bank_match_review").id, "form")],
            "target": "new",
            "context": dict(self.env.context, active_ids=self.usl_review_expense_ids.ids,
                            usl_employee_payment_review_id=self.id,
                            usl_bank_match_show_expense=len(self.usl_review_expense_ids) > 1),
        }

    def _usl_check_pending(self):
        self.ensure_one()
        self.check_access("write")
        if self.usl_review_completed or not self.usl_review_stage:
            raise UserError(_("This expense action is already complete. Refresh the expenses."))
        expenses = self.usl_review_expense_ids.exists()
        expenses.check_access("write")
        states = {"submit": {"draft"}, "approve": {"draft", "submitted"},
                  "post": {"approved"}, "post_entry": {"approved"}}
        if not expenses or any(expense.state not in states[self.usl_review_stage] for expense in expenses):
            raise UserError(_("The expense state changed. Refresh the expenses before continuing."))
        return expenses

    def action_continue_employee_payment(self):
        expenses = self._usl_check_pending()
        action = expenses._usl_employee_payment_review(self.usl_review_stage, wizard=self)
        if action:
            return action
        return self._usl_dispatch(expenses)

    def _usl_dispatch(self, expenses):
        stage = self.usl_review_stage
        expenses = expenses.with_context(usl_bank_match_transition=_POSTING_REVIEW_CONFIRMED)
        if stage in ("submit", "approve"):
            action = getattr(expenses, "action_" + stage)()
        elif stage == "post_entry" and all(expense.payment_mode == "own_account" for expense in expenses):
            action = self.with_context(active_ids=expenses.ids, usl_bank_match_transition=_POSTING_REVIEW_CONFIRMED).action_post_entry()
        else:
            action = expenses.action_post()
            if action and action.get("res_model") == "hr.expense.post.wizard":
                native = self.browse(action["res_id"])
                # Keep the employee evidence actually shown before native
                # company posting. Its effects can change competing matches.
                native.usl_review_fingerprint = self.usl_review_fingerprint
                if stage == "post_entry":
                    native.write({"employee_journal_id": self.employee_journal_id.id, "accounting_date": self.accounting_date})
                    action = native.with_context(action["context"], usl_bank_match_transition=False).action_post_entry()
        self.usl_review_completed = True
        action = action or self.usl_review_expense_ids._get_records_action()
        if action.get("context"):
            action["context"] = dict(action["context"])
            action["context"].pop("usl_bank_match_transition", None)
        return action

    def _usl_after_bank_match(self, expense):
        self.ensure_one()
        self.check_access("write")
        if self.usl_review_completed or expense not in self.usl_review_expense_ids:
            raise UserError(_("This expense action is already complete. Refresh the expenses."))
        self.usl_review_expense_ids -= expense
        if not self.usl_review_expense_ids:
            self.usl_review_completed = True
            return expense._get_records_action()
        self.usl_review_fingerprint = False
        return self.action_continue_employee_payment()

    def action_post_entry(self):
        self.ensure_one()
        expenses = self.env["hr.expense"].browse(self.env.context["active_ids"])
        expenses._check_can_create_move()
        if self.env.context.get("usl_bank_match_transition") is not _POSTING_REVIEW_CONFIRMED:
            action = expenses._usl_employee_payment_review("post_entry", wizard=self)
            if action:
                return action
        return super().action_post_entry()
