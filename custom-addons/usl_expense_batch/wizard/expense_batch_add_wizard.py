from odoo import _, fields, models
from odoo.exceptions import UserError


class UslExpenseBatchAddWizard(models.TransientModel):
    _name = "usl.expense.batch.add.wizard"
    _description = "Add Expenses to Batch"

    batch_id = fields.Many2one(
        "usl.expense.batch",
        required=True,
        readonly=True,
    )
    employee_id = fields.Many2one(related="batch_id.employee_id")
    company_id = fields.Many2one(related="batch_id.company_id")
    expense_ids = fields.Many2many(
        "hr.expense",
        string="Expenses",
        domain="[('employee_id', '=', employee_id), ('company_id', '=', company_id), ('expense_batch_id', '=', False), ('state', 'in', ['draft', 'approved', 'posted'])]",
    )

    def action_add(self):
        self.ensure_one()
        if not self.expense_ids:
            raise UserError(_("Select at least one expense to add."))
        self.batch_id.add_expenses(self.expense_ids.ids)
        return {"type": "ir.actions.act_window_close"}
