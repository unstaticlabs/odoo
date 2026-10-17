from odoo import _, fields, models

from ..models.expense_bank_matching import _POSTING_REVIEW_CONFIRMED


class HrExpensePostWizard(models.TransientModel):
    _inherit = "hr.expense.post.wizard"

    usl_review_expense_ids = fields.Many2many("hr.expense", readonly=True)
    usl_review_fingerprint = fields.Char(readonly=True)
    usl_review_evidence = fields.Text(string="Possible company payments", readonly=True)
    usl_review_has_matches = fields.Boolean(readonly=True)
    usl_review_matches = fields.Json(readonly=True)
    usl_review_from_entry = fields.Boolean(readonly=True)
    usl_review_changed = fields.Boolean(readonly=True)
    usl_review_employee_paid = fields.Boolean(readonly=True)

    def _usl_open_bank_match_review(self, expenses, fingerprint, evidence, details, *, from_entry, changed=False):
        self.ensure_one()
        self.write({
            "usl_review_expense_ids": [(6, 0, expenses.ids)],
            "usl_review_fingerprint": fingerprint,
            "usl_review_evidence": evidence,
            "usl_review_matches": details,
            "usl_review_has_matches": bool(details),
            "usl_review_from_entry": from_entry,
            "usl_review_changed": changed,
            "usl_review_employee_paid": any(expense.payment_mode != "company_account" for expense in expenses),
        })
        return {
            "type": "ir.actions.act_window",
            "name": _("Review possible company payments"),
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "views": [(self.env.ref("usl_accounting.view_expense_post_bank_match_review").id, "form")],
            "target": "new",
            "context": dict(self.env.context, active_ids=expenses.ids),
        }

    def action_confirm_bank_match_posting(self):
        self.ensure_one()
        expenses = self.usl_review_expense_ids.exists()
        expenses._check_can_create_move()
        fingerprint, evidence, details = expenses._usl_bank_match_posting_snapshot()
        if fingerprint != self.usl_review_fingerprint:
            return self._usl_open_bank_match_review(
                expenses, fingerprint, evidence, details,
                from_entry=self.usl_review_from_entry, changed=True,
            )
        confirmed_context = dict(
            self.env.context,
            active_ids=expenses.ids,
            usl_bank_match_posting_confirmation=_POSTING_REVIEW_CONFIRMED,
        )
        # A native edit can change Paid by while an employee wizard is open.
        # Dispatch again from the current payer set, never from the old dialog.
        action = expenses.with_context(confirmed_context).action_post()
        if action and action.get("res_model") == "hr.expense.post.wizard":
            employee_wizard = self.browse(action["res_id"])
            employee_wizard.write({
                "employee_journal_id": self.employee_journal_id.id,
                "accounting_date": self.accounting_date,
            })
            return employee_wizard.with_context(action["context"], **{
                "usl_bank_match_posting_confirmation": _POSTING_REVIEW_CONFIRMED,
            }).action_post_entry()
        return action or expenses._get_records_action(name=_("Posted expenses"))

    def action_review_expense_bank_matches(self):
        self.ensure_one()
        return self.usl_review_expense_ids._get_records_action(name=_("Review company payment matches"))

    def action_post_entry(self):
        self.ensure_one()
        expenses = self.env["hr.expense"].browse(self.env.context["active_ids"])
        expenses._check_can_create_move()
        if self.env.context.get("usl_bank_match_posting_confirmation") is not _POSTING_REVIEW_CONFIRMED:
            fingerprint, evidence, details = expenses._usl_bank_match_posting_snapshot()
            if evidence:
                return self._usl_open_bank_match_review(expenses, fingerprint, evidence, details, from_entry=True)
        return super().action_post_entry()
