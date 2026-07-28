from odoo import fields, models


class AccountMove(models.Model):
    _inherit = "account.move"

    expense_batch_id = fields.Many2one(
        "usl.expense.batch",
        string="Expense Batch",
        check_company=True,
        copy=False,
        index=True,
        readonly=True,
    )

    def action_open_expense_batch(self):
        self.ensure_one()
        if not self.expense_batch_id:
            return False
        return {
            "name": self.expense_batch_id.name,
            "type": "ir.actions.act_window",
            "res_model": "usl.expense.batch",
            "view_mode": "form",
            "views": [(False, "form")],
            "res_id": self.expense_batch_id.id,
        }
